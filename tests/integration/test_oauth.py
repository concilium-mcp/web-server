"""OAuth do MCP (plan-web-17): metadata, registro dinâmico, tela de consentimento, tokens com PKCE,
refresh rotativo, revogação e o portão do /mcp aceitando chave de API e token OAuth.

Usuários/coleções com prefixo "oa-" (o banco é compartilhado entre os módulos de integração).
"""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mcp_rag_api.config import get_settings
from mcp_rag_api.core import dash_auth, oauth
from mcp_rag_api.main import app
from mcp_rag_api.security import create_api_key

CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "senha-segura-123"


def _client(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dash.test", **kw)


async def _dash(username: str, role: str) -> httpx.AsyncClient:
    await dash_auth.create_user(username, PASSWORD, role)
    client = _client(headers={"X-Requested-With": "fetch"})
    resp = await client.post("/dash/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200
    return client


@pytest.fixture(scope="module")
async def admin():
    client = await _dash("oa-admin", "admin")
    await client.post("/dash/api/collections", json={"name": "oa-vendas"})
    await client.post("/dash/api/collections", json={"name": "oa-rh"})
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def editor():
    client = await _dash("oa-editor", "editor")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def viewer():
    client = await _dash("oa-viewer", "viewer")
    yield client
    await client.aclose()


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def _register(redirect: str = CLAUDE_CB, name: str = "Claude") -> httpx.Response:
    async with _client() as c:
        return await c.post(
            "/register",
            json={
                "redirect_uris": [redirect],
                "client_name": name,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )


async def _start(client_id: str, challenge: str, scope: str = "read write") -> str:
    """GET /authorize → id do pedido pendente (da URL da tela de consentimento)."""
    async with _client() as c:
        resp = await c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": CLAUDE_CB,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "st-123",
                "scope": scope,
                "resource": oauth.resource_url(),
            },
        )
    assert resp.status_code == 302, resp.text
    location = urlparse(resp.headers["location"])
    assert location.path == "/oauth/consent"
    return parse_qs(location.query)["request"][0]


async def _token(form: dict) -> httpx.Response:
    async with _client() as c:
        return await c.post("/token", data=form)


async def _connect(dash: httpx.AsyncClient, *, scopes=("read", "write"), collections=(), agent_slug=None) -> dict:
    """Fluxo completo: registro → authorize → consentimento → token. Devolve os tokens + client_id."""
    client_id = (await _register()).json()["client_id"]
    verifier, challenge = _pkce()
    request_id = await _start(client_id, challenge)
    resp = await dash.post(
        f"/oauth/api/requests/{request_id}/decision",
        json={"approve": True, "scopes": list(scopes), "collections": list(collections), "agent_slug": agent_slug},
    )
    assert resp.status_code == 200, resp.text
    back = urlparse(resp.json()["redirect"])
    params = parse_qs(back.query)
    assert f"{back.scheme}://{back.netloc}{back.path}" == CLAUDE_CB and params["state"] == ["st-123"]
    resp = await _token(
        {
            "grant_type": "authorization_code",
            "code": params["code"][0],
            "redirect_uri": CLAUDE_CB,
            "client_id": client_id,
            "code_verifier": verifier,
        }
    )
    assert resp.status_code == 200, resp.text
    return {**resp.json(), "client_id": client_id, "code": params["code"][0], "verifier": verifier}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- descoberta


async def test_metadata_and_mcp_challenge():
    base = get_settings().public_url.rstrip("/")
    async with _client() as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert meta["issuer"] == base  # sem barra no fim: tem que bater exato (RFC 8414)
        assert meta["authorization_endpoint"] == f"{base}/authorize"
        assert meta["registration_endpoint"] == f"{base}/register"
        assert meta["code_challenge_methods_supported"] == ["S256"]
        prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert prm["resource"] == f"{base}/mcp" and prm["authorization_servers"] == [base]

        assert (await c.get("/.well-known/openid-configuration")).status_code == 404  # não cai no portão

        resp = await c.post("/mcp", json={})
        assert resp.status_code == 401
        challenge = resp.headers["WWW-Authenticate"]
        assert f'resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"' in challenge


# ---------------------------------------------------------------- registro dinâmico


async def test_register_allowlist():
    assert (await _register(CLAUDE_CB)).status_code == 201
    assert (await _register("https://claude.com/api/mcp/auth_callback")).status_code == 201
    assert (await _register("http://localhost:33418/callback")).status_code == 201  # Claude Code
    assert (await _register("https://chatgpt.com/connector/oauth/7DoEaYwjkfwH")).status_code == 201  # ChatGPT
    assert (await _register("https://vscode.dev/redirect")).status_code == 201
    assert (await _register("cursor://anysphere.cursor-mcp/oauth/callback")).status_code == 201  # app nativo
    bad_uris = [
        "https://evil.example/cb",
        "http://claude.ai/cb",
        "https://claude.ai.evil.example/cb",
        "https://notchatgpt.com/cb",
        "evilapp://callback",
        "javascript://alert(1)",
        # plan-web-32: subdomínio ou outro caminho dos sites liberados também não
        "https://x.claude.ai/api/mcp/auth_callback",
        "https://claude.ai/outra/pagina",
        "https://chatgpt.com/share/abc",
    ]
    for bad in bad_uris:
        resp = await _register(bad)
        assert resp.status_code == 400 and resp.json()["error"] == "invalid_redirect_uri", bad


# ---------------------------------------------------------------- consentimento + tokens


async def test_full_flow_and_tokens_work_on_rest(admin, editor):
    anon = await _client().__aenter__()
    client_id = (await _register()).json()["client_id"]
    _, challenge = _pkce()
    request_id = await _start(client_id, challenge)
    info = (await anon.get(f"/oauth/api/requests/{request_id}")).json()
    assert info["user"] is None and info["client_name"] == "Claude" and info["redirect_host"] == "claude.ai"
    assert info["redirect_uri"] == CLAUDE_CB  # a tela mostra o endereço inteiro, não só o host
    assert info["requested_scopes"] == ["read", "write"]
    await anon.aclose()
    info = (await editor.get(f"/oauth/api/requests/{request_id}")).json()
    assert info["user"]["username"] == "oa-editor" and info["available_scopes"] == ["read", "write"]
    assert info["agents"] == []  # conectar como agente é só para admin

    tokens = await _connect(editor)
    assert tokens["access_token"].startswith("kb_at_") and tokens["refresh_token"].startswith("kb_rt_")
    assert tokens["scope"] == "read write" and tokens["token_type"] == "Bearer"
    async with _client() as c:
        resp = await c.get("/collections", headers=_bearer(tokens["access_token"]))
        assert resp.status_code == 200
        assert {"oa-vendas", "oa-rh"} <= {col["name"] for col in resp.json()}


async def test_code_is_single_use_and_needs_pkce(editor):
    tokens = await _connect(editor)
    reuse = await _token(
        {
            "grant_type": "authorization_code",
            "code": tokens["code"],
            "redirect_uri": CLAUDE_CB,
            "client_id": tokens["client_id"],
            "code_verifier": tokens["verifier"],
        }
    )
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
    # reapresentar o código derruba a conexão inteira
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(tokens["access_token"]))).status_code == 403

    client_id = (await _register()).json()["client_id"]
    _, challenge = _pkce()
    request_id = await _start(client_id, challenge)
    back = (
        await editor.post(f"/oauth/api/requests/{request_id}/decision", json={"approve": True, "scopes": ["read"]})
    ).json()
    code = parse_qs(urlparse(back["redirect"]).query)["code"][0]
    wrong = await _token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CLAUDE_CB,
            "client_id": client_id,
            "code_verifier": "x" * 50,
        }
    )
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"


async def test_deny_returns_access_denied(editor):
    client_id = (await _register()).json()["client_id"]
    _, challenge = _pkce()
    request_id = await _start(client_id, challenge)
    resp = await editor.post(f"/oauth/api/requests/{request_id}/decision", json={"approve": False})
    params = parse_qs(urlparse(resp.json()["redirect"]).query)
    assert params["error"] == ["access_denied"] and params["state"] == ["st-123"]
    # pedido é de uso único
    assert (await editor.get(f"/oauth/api/requests/{request_id}")).status_code == 404


async def test_scopes_capped_by_role(viewer, editor):
    client_id = (await _register()).json()["client_id"]
    _, challenge = _pkce()
    request_id = await _start(client_id, challenge)
    resp = await viewer.post(
        f"/oauth/api/requests/{request_id}/decision", json={"approve": True, "scopes": ["read", "write"]}
    )
    assert resp.status_code == 403

    request_id = await _start(client_id, challenge)
    resp = await editor.post(f"/oauth/api/requests/{request_id}/decision", json={"approve": True, "scopes": ["write"]})
    assert resp.status_code == 400  # "read" é obrigatória

    tokens = await _connect(editor, scopes=["read"], collections=["oa-vendas"])
    async with _client() as c:
        headers = _bearer(tokens["access_token"])
        created = await c.post("/collections", json={"name": "oa-nova"}, headers=headers)
        assert created.status_code == 403  # sem write
        search = await c.post("/search", json={"query": "x", "collections": ["oa-rh"]}, headers=headers)
        assert search.status_code == 403  # fora das pastas concedidas
        search = await c.post("/search", json={"query": "x", "collections": ["oa-vendas"]}, headers=headers)
        assert search.status_code == 200


async def test_connect_as_agent(admin):
    resp = await admin.post(
        "/dash/api/agents",
        json={"slug": "oa-comercial", "name": "Comercial", "system_prompt": "Vende.", "scopes": ["read"]},
    )
    assert resp.status_code == 200, resp.text
    tokens = await _connect(admin, scopes=["read"], agent_slug="oa-comercial")
    async with _client() as c:
        resp = await c.get("/agents/oa-comercial/context", headers=_bearer(tokens["access_token"]))
        assert resp.status_code == 200, resp.text
    # arquivar o agente invalida a conexão
    assert (await admin.post("/dash/api/agents/oa-comercial/archive", json={})).status_code == 200
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(tokens["access_token"]))).status_code == 403


# ---------------------------------------------------------------- refresh e revogação


async def test_refresh_rotates_and_reuse_revokes(editor):
    tokens = await _connect(editor)
    form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": tokens["client_id"]}
    first = await _token(form)
    assert first.status_code == 200, first.text
    new = first.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(tokens["access_token"]))).status_code == 403  # antigo
        assert (await c.get("/collections", headers=_bearer(new["access_token"]))).status_code == 200
    # refresh antigo reapresentado = vazou: conexão revogada
    assert (await _token(form)).status_code == 400
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(new["access_token"]))).status_code == 403


async def test_reuse_detection_survives_cleanup(editor):
    """O cleanup (agora periódico) não apaga refresh revogado antes de vencer: o replay ainda derruba a conexão."""
    tokens = await _connect(editor)
    form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": tokens["client_id"]}
    new = (await _token(form)).json()
    await oauth.cleanup()
    assert (await _token(form)).status_code == 400
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(new["access_token"]))).status_code == 403


async def test_connections_list_and_revoke(admin, editor):
    tokens = await _connect(editor)
    grants = (await editor.get("/dash/api/oauth/grants")).json()
    mine = [g for g in grants if g["client_name"] == "Claude"]
    assert mine and all(g["username"] == "oa-editor" for g in grants)
    # admin vê de todos; editor não pode pedir "all"
    assert (await editor.get("/dash/api/oauth/grants", params={"all": "true"})).status_code == 403
    all_grants = (await admin.get("/dash/api/oauth/grants", params={"all": "true"})).json()
    assert {g["id"] for g in mine} <= {g["id"] for g in all_grants}

    newest = mine[0]["id"]
    assert (await editor.post(f"/dash/api/oauth/grants/{newest}/revoke")).status_code == 200
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(tokens["access_token"]))).status_code == 403


async def test_revoke_endpoint_and_role_change(admin):
    other = await _dash("oa-rebaixado", "editor")
    tokens = await _connect(other)
    async with _client() as c:
        # o RevocationRequest do SDK 2.2 exige o campo client_secret mesmo para cliente público (vazio)
        form = {"token": tokens["refresh_token"], "client_id": tokens["client_id"], "client_secret": ""}
        resp = await c.post("/revoke", data=form)
        assert resp.status_code == 200
        assert (await c.get("/collections", headers=_bearer(tokens["access_token"]))).status_code == 403

    # nova sessão (a anterior segue valendo) → nova conexão; rebaixar o papel derruba
    tokens = await _connect(other)
    user_id = (await other.get("/dash/api/auth/me")).json()["id"]
    assert (await admin.patch(f"/dash/api/users/{user_id}", json={"role": "viewer"})).status_code == 200
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(tokens["access_token"]))).status_code == 403
    await other.aclose()


# ---------------------------------------------------------------- portão do /mcp


async def test_mcp_gate_accepts_api_key_and_oauth(editor):
    async with _client() as c:
        tokens = await _connect(editor)
        bad = await c.post("/mcp", json={}, headers=_bearer("kb_at_invalido"))
        assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["WWW-Authenticate"]
        # passa do portão (o que vem depois é o app MCP; aqui só importa não ser 401)
        assert (await c.get("/collections", headers=_bearer(tokens["access_token"]))).status_code == 200

    from mcp_rag_api import db

    async with db.pool().acquire() as conn:
        key = await create_api_key(conn, label="oa-legado", scopes=["read"])
    async with _client() as c:
        assert (await c.get("/collections", headers=_bearer(key["api_key"]))).status_code == 200


async def test_consent_page_is_static_and_csp(editor):
    resp = await editor.get("/oauth/consent", params={"request": "x"})
    assert resp.status_code == 200
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"
    assert "Cross-Origin-Opener-Policy" not in resp.headers  # o popup do cliente OAuth precisa do opener
    assert "<script>" not in resp.text  # sem script inline (CSP)
    assert (await editor.get("/oauth/api/requests/inexistente")).status_code == 404
