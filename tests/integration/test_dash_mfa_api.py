"""2FA (TOTP) da dashboard ponta a ponta: ativar, login em duas etapas, reuso de código,
recovery code, desativar e reset pelo admin. Usuários com prefixo "mfa-"."""

import httpx
import pytest

from mcp_rag_api.core import dash_auth, mfa
from mcp_rag_api.main import app

DASH_HEADERS = {"X-Requested-With": "fetch"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dash.test", headers=DASH_HEADERS)


async def _login(c: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await c.post("/dash/api/auth/login", json={"username": username, "password": password})


def _code(secret: str, offset: int = 0) -> str:
    return mfa.totp_at(secret, mfa.current_step() + offset)


async def _enable(c: httpx.AsyncClient, password: str) -> tuple[str, list[str]]:
    setup = await c.post("/dash/api/account/2fa/setup", json={"password": password})
    assert setup.status_code == 200
    body = setup.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in body["qr_svg"]
    resp = await c.post("/dash/api/account/2fa/enable", json={"code": _code(body["secret"], -1)})
    assert resp.status_code == 200
    return body["secret"], resp.json()["recovery_codes"]


async def test_enable_then_login_requires_code():
    user, password = "mfa-ana", "senha-da-ana-1"
    await dash_auth.create_user(user, password, "editor")
    async with _client() as c:
        assert (await _login(c, user, password)).status_code == 200
        assert (await c.get("/dash/api/account/2fa")).json()["enabled"] is False
        # senha errada não inicia o cadastro
        assert (await c.post("/dash/api/account/2fa/setup", json={"password": "errada-123"})).status_code == 400
        secret, codes = await _enable(c, password)
        assert len(codes) == mfa.RECOVERY_CODES
        status = (await c.get("/dash/api/account/2fa")).json()
        assert status["enabled"] is True and status["recovery_codes_left"] == mfa.RECOVERY_CODES

    async with _client() as c:
        first = await _login(c, user, password)
        assert first.status_code == 200
        assert first.json()["mfa_required"] is True
        assert dash_auth.SESSION_COOKIE not in first.cookies  # sem sessão antes do código
        token = first.json()["mfa_token"]
        assert (await c.get("/dash/api/auth/me")).status_code == 401

        bad = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": "000000"})
        assert bad.status_code == 400
        ok = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": _code(secret)})
        assert ok.status_code == 200
        assert ok.json()["username"] == user
        assert (await c.get("/dash/api/auth/me")).status_code == 200
        # desafio é de uso único
        again = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": _code(secret, 1)})
        assert again.status_code == 401


async def test_same_code_cannot_be_used_twice():
    user, password = "mfa-bia", "senha-da-bia-2"
    await dash_auth.create_user(user, password, "viewer")
    async with _client() as c:
        await _login(c, user, password)
        secret, _ = await _enable(c, password)
    code = _code(secret, 1)
    async with _client() as c:
        token = (await _login(c, user, password)).json()["mfa_token"]
        assert (await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": code})).status_code == 200
    async with _client() as c:
        token = (await _login(c, user, password)).json()["mfa_token"]
        assert (await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": code})).status_code == 400


async def test_challenge_dies_after_max_attempts():
    user, password = "mfa-caio", "senha-do-caio-3"
    await dash_auth.create_user(user, password, "viewer")
    async with _client() as c:
        await _login(c, user, password)
        secret, _ = await _enable(c, password)
    async with _client() as c:
        token = (await _login(c, user, password)).json()["mfa_token"]
        for _ in range(mfa.CHALLENGE_MAX_ATTEMPTS):
            await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": "000000"})
        # até o código certo é recusado: recomeça pela senha
        resp = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": _code(secret)})
        assert resp.status_code in (401, 429)


async def test_recovery_code_works_once_and_regenerate_invalidates_old():
    user, password = "mfa-duda", "senha-da-duda-4"
    await dash_auth.create_user(user, password, "viewer")
    async with _client() as c:
        await _login(c, user, password)
        secret, codes = await _enable(c, password)
    async with _client() as c:
        token = (await _login(c, user, password)).json()["mfa_token"]
        resp = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": codes[0].upper()})
        assert resp.status_code == 200
        assert (await c.get("/dash/api/account/2fa")).json()["recovery_codes_left"] == mfa.RECOVERY_CODES - 1
        regen = await c.post("/dash/api/account/2fa/recovery-codes", json={"code": codes[1]})
        assert regen.status_code == 200
        new_codes = regen.json()["recovery_codes"]
    async with _client() as c:
        token = (await _login(c, user, password)).json()["mfa_token"]
        for old in codes[:3]:  # já usado e anteriores à troca: nenhum vale
            assert (await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": old})).status_code == 400
        resp = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": new_codes[0]})
        assert resp.status_code == 200


async def test_disable_needs_password_and_code():
    user, password = "mfa-edu", "senha-do-edu-5"
    await dash_auth.create_user(user, password, "viewer")
    async with _client() as c:
        await _login(c, user, password)
        secret, _ = await _enable(c, password)
        url = "/dash/api/account/2fa/disable"
        assert (await c.post(url, json={"password": "errada-123", "code": _code(secret)})).status_code == 400
        assert (await c.post(url, json={"password": password, "code": "000000"})).status_code == 400
        assert (await c.post(url, json={"password": password, "code": _code(secret)})).status_code == 200
        assert (await c.get("/dash/api/account/2fa")).json()["enabled"] is False
    async with _client() as c:
        resp = await _login(c, user, password)
        assert resp.status_code == 200 and "mfa_required" not in resp.json()


@pytest.fixture
async def mfa_admin():
    await dash_auth.create_user("mfa-admin", "senha-do-admin-6", "admin")
    async with _client() as c:
        assert (await _login(c, "mfa-admin", "senha-do-admin-6")).status_code == 200
        yield c


async def test_admin_reset_turns_off_2fa_and_ends_sessions(mfa_admin):
    user, password = "mfa-fabi", "senha-da-fabi-7"
    created = await dash_auth.create_user(user, password, "viewer")
    async with _client() as c:
        await _login(c, user, password)
        await _enable(c, password)
        users = {u["username"]: u for u in (await mfa_admin.get("/dash/api/users")).json()}
        assert users[user]["totp_enabled"] is True
        assert (await mfa_admin.delete(f"/dash/api/users/{created['id']}/2fa")).status_code == 200
        assert (await c.get("/dash/api/auth/me")).status_code == 401  # sessão derrubada
    async with _client() as c:
        resp = await _login(c, user, password)
        assert resp.status_code == 200 and "mfa_required" not in resp.json()


async def test_setup_refused_while_enabled():
    user, password = "mfa-gabi", "senha-da-gabi-8"
    await dash_auth.create_user(user, password, "viewer")
    async with _client() as c:
        await _login(c, user, password)
        await _enable(c, password)
        assert (await c.post("/dash/api/account/2fa/setup", json={"password": password})).status_code == 400


async def _stored_secret(username: str) -> str:
    from mcp_rag_api.db import pool

    return await pool().fetchval("SELECT totp_secret FROM dash_users WHERE username = $1", username)


async def test_encrypted_secrets_and_reseal_of_legacy_plaintext(monkeypatch):
    from mcp_rag_api.config import get_settings

    # conta antiga, cadastrada sem chave: segredo em texto puro
    legacy, legacy_pw = "mfa-hugo", "senha-do-hugo-9"
    await dash_auth.create_user(legacy, legacy_pw, "viewer")
    async with _client() as c:
        await _login(c, legacy, legacy_pw)
        legacy_secret, _ = await _enable(c, legacy_pw)
    assert await _stored_secret(legacy) == legacy_secret

    monkeypatch.setenv("DASH_TOTP_ENCRYPTION_KEY", "chave-de-teste-" + "x" * 32)
    get_settings.cache_clear()
    try:
        # conta nova com a chave ligada: vai cifrada e o login funciona
        user, password = "mfa-iris", "senha-da-iris-10"
        await dash_auth.create_user(user, password, "viewer")
        async with _client() as c:
            await _login(c, user, password)
            secret, _ = await _enable(c, password)
        stored = await _stored_secret(user)
        assert stored.startswith(mfa.ENC_PREFIX) and secret not in stored
        async with _client() as c:
            token = (await _login(c, user, password)).json()["mfa_token"]
            resp = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": _code(secret, 1)})
            assert resp.status_code == 200

        # a conta antiga segue entrando antes da migração, e a migração cifra só o que falta
        assert await mfa.reseal_all() >= 1
        assert (await _stored_secret(legacy)).startswith(mfa.ENC_PREFIX)
        assert await _stored_secret(user) == stored  # já cifrado: não mexe
        async with _client() as c:
            token = (await _login(c, legacy, legacy_pw)).json()["mfa_token"]
            resp = await c.post("/dash/api/auth/login/mfa", json={"mfa_token": token, "code": _code(legacy_secret, 1)})
            assert resp.status_code == 200

        # --decrypt devolve o texto puro (para trocar ou remover a chave)
        await mfa.reseal_all(decrypt=True)
        assert await _stored_secret(legacy) == legacy_secret
        assert await _stored_secret(user) == secret
    finally:
        get_settings.cache_clear()
