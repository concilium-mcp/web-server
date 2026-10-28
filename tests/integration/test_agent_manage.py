"""Gerenciar agente pela dash (plan-web-16): detalhe, editar (nova versão), chaves e arquivar.

Agentes com prefixo "am-" (o banco é compartilhado entre os módulos de integração).
"""

import httpx
import pytest

from mcp_rag_api.core import dash_auth
from mcp_rag_api.main import app


async def _client(username: str, role: str) -> httpx.AsyncClient:
    await dash_auth.create_user(username, "senha-segura-123", role)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://dash.test",
        headers={"X-Requested-With": "fetch"},
    )
    resp = await client.post("/dash/api/auth/login", json={"username": username, "password": "senha-segura-123"})
    assert resp.status_code == 200
    return client


@pytest.fixture(scope="module")
async def admin():
    client = await _client("am-admin", "admin")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def editor():
    client = await _client("am-editor", "editor")
    yield client
    await client.aclose()


async def _create(admin, slug: str) -> dict:
    resp = await admin.post(
        "/dash/api/agents",
        json={"slug": slug, "name": "Suporte", "system_prompt": "Você é o suporte.", "scopes": ["read", "write"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_detail_has_profile_keys_and_history(admin):
    created = await _create(admin, "am-suporte")
    detail = (await admin.get("/dash/api/agents/am-suporte")).json()
    assert detail["agent"]["system_prompt"] == "Você é o suporte."
    assert detail["agent"]["auto_apply_updates"] is False
    assert len(detail["api_keys"]) == 1 and created["api_key"].startswith(detail["api_keys"][0]["prefix"])
    assert "api_key" not in detail["api_keys"][0] and "key_hash" not in detail["api_keys"][0]  # segredo nunca volta
    assert detail["history"][0]["version"] == 1


async def test_edit_creates_new_version(admin):
    resp = await admin.patch(
        "/dash/api/agents/am-suporte",
        json={
            "name": "Suporte N1",
            "description": "Atende clientes",
            "system_prompt": "Você é o suporte nível 1.",
            "scopes": ["read"],
            "allowed_collections": ["am-docs"],
            "auto_apply_updates": True,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2
    detail = (await admin.get("/dash/api/agents/am-suporte")).json()
    agent = detail["agent"]
    assert agent["name"] == "Suporte N1" and agent["scopes"] == ["read"] and agent["allowed_collections"] == ["am-docs"]
    assert agent["auto_apply_updates"] is True
    assert detail["history"][0]["version"] == 2 and detail["history"][0]["change_note"] == "edição pela dashboard"

    assert (await admin.patch("/dash/api/agents/am-suporte", json={"scopes": []})).status_code == 400


async def test_editor_cannot_manage(editor):
    assert (await editor.get("/dash/api/agents/am-suporte")).status_code == 403
    assert (await editor.patch("/dash/api/agents/am-suporte", json={"name": "x"})).status_code == 403
    assert (await editor.post("/dash/api/agents/am-suporte/keys", json={})).status_code == 403
    assert (await editor.post("/dash/api/agents/am-suporte/archive", json={})).status_code == 403


async def test_issue_and_revoke_agent_key(admin):
    issued = await admin.post("/dash/api/agents/am-suporte/keys", json={"label": "notebook"})
    assert issued.status_code == 200
    body = issued.json()
    assert body["api_key"].startswith("kb_sk_") and "kb-am-suporte" in body["connect_command"]

    keys = (await admin.get("/dash/api/agents/am-suporte")).json()["api_keys"]
    assert len([k for k in keys if not k["revoked_at"]]) == 2
    new = next(k for k in keys if k["label"] == "notebook")
    assert (await admin.post(f"/dash/api/keys/{new['id']}/revoke")).status_code == 200
    keys = (await admin.get("/dash/api/agents/am-suporte")).json()["api_keys"]
    assert next(k for k in keys if k["id"] == new["id"])["revoked_at"] is not None


async def test_archive_revokes_all_keys(admin):
    resp = await admin.post("/dash/api/agents/am-suporte/archive", json={})
    assert resp.status_code == 200 and resp.json()["archived"] is True
    detail = (await admin.get("/dash/api/agents/am-suporte")).json()
    assert detail["agent"]["status"] == "archived"
    assert all(k["revoked_at"] for k in detail["api_keys"])
    listed = next(a for a in (await admin.get("/dash/api/agents")).json() if a["slug"] == "am-suporte")
    assert listed["status"] == "archived" and listed["active_keys"] == 0
