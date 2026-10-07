"""Pacote de segurança da dashboard (plan-web-04): cookie Secure, CSRF por header,
rate limit de login, revogação de sessão em troca de papel, headers de segurança/CSP
e validação de nome de coleção (base do XSS armazenado).

Roda contra Postgres real (ver tests/integration/conftest.py). Usuários e coleções
com prefixo "sec-" para não interferir com os demais módulos.
"""

import os

import httpx
import pytest

from mcp_rag_api.config import get_settings
from mcp_rag_api.core import dash_auth
from mcp_rag_api.main import app

DASH_HEADERS = {"X-Requested-With": "fetch"}


async def _client(base_url: str = "http://dash.test", headers: dict | None = DASH_HEADERS) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url, headers=headers)


@pytest.fixture(scope="module")
async def admin():
    await dash_auth.create_user("sec-admin", "senha-do-admin-1", "admin")
    client = await _client()
    resp = await client.post("/dash/api/auth/login", json={"username": "sec-admin", "password": "senha-do-admin-1"})
    assert resp.status_code == 200
    yield client
    await client.aclose()


# ---------------------------------------------------------------- headers de segurança


async def test_security_headers_on_all_responses():
    async with await _client() as c:
        for path in ["/dashboard", "/static/js/main.js", "/health"]:
            resp = await c.get(path)
            assert resp.status_code == 200
            assert resp.headers["X-Content-Type-Options"] == "nosniff"
            assert resp.headers["Referrer-Policy"] == "same-origin"
            assert resp.headers["X-Frame-Options"] == "DENY"
            csp = resp.headers["Content-Security-Policy"]
            assert "default-src 'self'" in csp
            assert "script-src 'self'" in csp
            assert "style-src 'self' 'unsafe-inline'" in csp
            assert "img-src 'self' data:" in csp
            assert "connect-src 'self'" in csp


# ---------------------------------------------------------------- cookie Secure


async def test_session_cookie_secure_only_over_https():
    body = {"username": "sec-https", "password": "senha-do-https-2"}
    await dash_auth.create_user(body["username"], body["password"], "viewer")

    # HTTP puro (dev local): cookie sem Secure, sessão funciona
    async with await _client(base_url="http://dash.test") as c:
        resp = await c.post("/dash/api/auth/login", json=body)
        assert resp.status_code == 200
        assert "Secure" not in resp.headers["set-cookie"]

    # HTTPS: cookie com Secure
    async with await _client(base_url="https://dash.test") as c:
        resp = await c.post("/dash/api/auth/login", json=body)
        assert resp.status_code == 200
        assert "Secure" in resp.headers["set-cookie"]
        assert (await c.get("/dash/api/auth/me")).status_code == 200

    # atrás de proxy: X-Forwarded-Proto https sobre conexão http local
    async with await _client(base_url="http://dash.test") as c:
        resp = await c.post("/dash/api/auth/login", json=body, headers={**DASH_HEADERS, "X-Forwarded-Proto": "https"})
        assert resp.status_code == 200
        assert "Secure" in resp.headers["set-cookie"]


# ---------------------------------------------------------------- CSRF (X-Requested-With)


async def test_mutations_require_csrf_header(admin):
    # login sem o header: 403 antes mesmo de conferir credencial
    async with await _client(headers=None) as c:
        resp = await c.post("/dash/api/auth/login", json={"username": "x", "password": "yyyyyyyy"})
        assert resp.status_code == 403

    # header com valor errado também é rejeitado
    async with await _client(headers={"X-Requested-With": "XMLHttpRequest"}) as c:
        resp = await c.post("/dash/api/auth/login", json={"username": "x", "password": "yyyyyyyy"})
        assert resp.status_code == 403

    # GET não é mutação: segue sem o header
    async with await _client(headers=None) as c:
        assert (await c.get("/dash/api/auth/me")).status_code == 401  # sem sessão, mas passou do CSRF

    # mutação autenticada sem o header: 403 (não executa o endpoint)
    cookie = admin.cookies.get(dash_auth.SESSION_COOKIE)
    async with await _client(headers=None) as c:
        c.cookies.set(dash_auth.SESSION_COOKIE, cookie)
        assert (await c.post("/dash/api/search-test", json={"query": "alfa"})).status_code == 403
    # com o header: executa normalmente
    assert (await admin.post("/dash/api/search-test", json={"query": "alfa"})).status_code == 200


# ---------------------------------------------------------------- rate limit do login


async def test_login_rate_limit_blocks_then_env_disables():
    body = {"username": "sec-brute", "password": "senha-correta-3"}
    await dash_auth.create_user(body["username"], body["password"], "viewer")
    wrong = {**body, "password": "senha-errada-999"}

    async with await _client() as c:
        for _ in range(5):
            assert (await c.post("/dash/api/auth/login", json=wrong)).status_code == 401
        # 6ª tentativa: bloqueado com 429 e mensagem de espera
        resp = await c.post("/dash/api/auth/login", json=wrong)
        assert resp.status_code == 429
        assert "Tente novamente" in resp.json()["detail"]

    # DASH_LOGIN_RATE_LIMIT=0 desliga o limiter (dev): a mesma chave volta a responder 401
    os.environ["DASH_LOGIN_RATE_LIMIT"] = "0"
    get_settings.cache_clear()
    try:
        async with await _client() as c:
            assert (await c.post("/dash/api/auth/login", json=wrong)).status_code == 401
    finally:
        del os.environ["DASH_LOGIN_RATE_LIMIT"]
        get_settings.cache_clear()

    # limiter reconstruído com o padrão (5): sucesso zera o contador da chave
    async with await _client() as c:
        for _ in range(4):
            assert (await c.post("/dash/api/auth/login", json=wrong)).status_code == 401
        assert (await c.post("/dash/api/auth/login", json=body)).status_code == 200
        for _ in range(4):  # sem o reset estas tentativas já estariam bloqueadas
            assert (await c.post("/dash/api/auth/login", json=wrong)).status_code == 401
        assert (await c.post("/dash/api/auth/login", json=wrong)).status_code == 401  # 5ª falha ainda executa
        assert (await c.post("/dash/api/auth/login", json=wrong)).status_code == 429  # a próxima morre no bloqueio


# ---------------------------------------------------------------- downgrade de papel


async def test_role_downgrade_revokes_sessions(admin):
    target = {"username": "sec-downgrade", "password": "senha-do-down-4"}
    user = await dash_auth.create_user(target["username"], target["password"], "editor")

    victim = await _client()
    assert (await victim.post("/dash/api/auth/login", json=target)).status_code == 200
    assert (await victim.get("/dash/api/auth/me")).status_code == 200

    # rebaixa editor → viewer: a sessão ativa morre na hora
    resp = await admin.patch(f"/dash/api/users/{user['id']}", json={"role": "viewer"})
    assert resp.status_code == 200
    assert (await victim.get("/dash/api/auth/me")).status_code == 401

    # login de novo funciona (o usuário não está desativado)
    assert (await victim.post("/dash/api/auth/login", json=target)).status_code == 200
    me = (await victim.get("/dash/api/auth/me")).json()
    assert me["role"] == "viewer"
    await victim.aclose()


# ---------------------------------------------------------------- nome de coleção


async def test_collection_name_validation(admin):
    # nomes válidos: unicode, espaço, dígito, traço (desde que não no início)
    for name in ["sec-relatórios 2026", "sec_minha_coleção", "sec-vendas"]:
        resp = await admin.post("/dash/api/collections", json={"name": name})
        assert resp.status_code == 200, name

    # tentativa de XSS armazenado: rejeitado com 400 claro
    # (tamanho é barrado antes pelo pydantic; o charset é o que o core valida aqui)
    for bad in ["<script>alert(1)</script>", "x<img src=1 onerror=alert(1)>", "-começa-com-traco", "nome/com/slash"]:
        resp = await admin.post("/dash/api/collections", json={"name": bad})
        assert resp.status_code == 400, bad
        assert "inválido" in resp.json()["detail"]
