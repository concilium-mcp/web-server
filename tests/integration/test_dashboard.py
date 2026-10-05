"""Dashboard (plan-web-01): centroide, grafo por cosseno, auth da dash, gestão e extras.

Roda contra Postgres real (ver tests/integration/conftest.py). Os endpoints são
exercitados via ASGITransport no mesmo loop da fixture de banco — o pool já está
inicializado pelo conftest, então o lifespan do app não precisa rodar.

O vocabulário dos documentos aqui é propositalmente disjunto do test_flows.py:
o banco é compartilhado entre os módulos e a busca híbrida lá não filtra collection.
"""

import uuid

import httpx
import numpy as np
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import agents, dash_auth, documents
from mcp_rag_api.main import app
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import PermissionDenied, resolve_key

# A e B compartilham quase todas as palavras (cosseno alto); C é distante de ambos.
FONTE_A = "alfa bravo charlie delta echo foxtrot"
FONTE_B = "alfa bravo charlie delta echo foxtrot golf hotel"
FONTE_C = "mike november oscar papa quebec romeo"

ADMIN_USER = {"username": "dash-admin", "password": "senha-muito-secreta"}


@pytest.fixture(scope="module")
async def seeded_docs():
    await documents.create_collection(ADMIN, "grafos")
    a = await documents.add_document(ADMIN, "grafos", "Alfa", FONTE_A)
    b = await documents.add_document(ADMIN, "grafos", "Bravo", FONTE_B, force=True)
    c = await documents.add_document(ADMIN, "grafos", "Mike", FONTE_C, force=True)
    return {"a": a["document_id"], "b": b["document_id"], "c": c["document_id"]}


async def dash_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dash.test")


async def principal_for(api_key: str):
    async with db.pool().acquire() as conn:
        return await resolve_key(conn, api_key)


@pytest.fixture(scope="module")
async def admin_client(seeded_docs):
    await dash_auth.create_user(ADMIN_USER["username"], ADMIN_USER["password"], "admin")
    client = await dash_client()
    resp = await client.post("/dash/api/auth/login", json=ADMIN_USER)
    assert resp.status_code == 200
    yield client
    await client.aclose()


async def test_auth_login_me_logout(seeded_docs, admin_client):
    # sem sessão: rotas protegidas barram com 401
    async with await dash_client() as anon:
        assert (await anon.get("/dash/api/auth/me")).status_code == 401
        assert (await anon.get("/dash/api/graph")).status_code == 401

    # senha errada não loga
    async with await dash_client() as c:
        resp = await c.post(
            "/dash/api/auth/login", json={"username": ADMIN_USER["username"], "password": "errada"}
        )
        assert resp.status_code == 401

    # login → cookie HttpOnly → me → logout → sessão morre
    async with await dash_client() as c:
        resp = await c.post("/dash/api/auth/login", json=ADMIN_USER)
        assert resp.status_code == 200
        cookie = resp.cookies.get(dash_auth.SESSION_COOKIE)
        assert cookie
        set_cookie = resp.headers.get("set-cookie", "")
        assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie
        # só o hash do token vai para o banco
        stored = await db.pool().fetchval("SELECT token_hash FROM dash_sessions")
        assert stored != cookie and len(stored) == 64
        me = await c.get("/dash/api/auth/me")
        assert me.json()["username"] == ADMIN_USER["username"]
        assert me.json()["role"] == "admin"
        assert (await c.post("/dash/api/auth/logout")).status_code == 200
        assert (await c.get("/dash/api/auth/me")).status_code == 401


async def test_session_expiration_and_disabled_user(seeded_docs):
    user = await dash_auth.create_user("expira", "senha-muito-secreta", "viewer")
    token = await dash_auth.create_session(user["id"])
    assert (await dash_auth.resolve_session(token)) is not None
    # expira no passado → sessão inválida
    await db.pool().execute(
        "UPDATE dash_sessions SET expires_at = now() - interval '1 second' WHERE user_id = $1",
        uuid.UUID(user["id"]),
    )
    assert (await dash_auth.resolve_session(token)) is None
    # usuário desativado perde a sessão, mesmo válida
    token2 = await dash_auth.create_session(user["id"])
    await db.pool().execute("UPDATE dash_users SET disabled_at = now() WHERE id = $1", uuid.UUID(user["id"]))
    assert (await dash_auth.resolve_session(token2)) is None


async def test_centroid_written_on_ingest_and_reindex(seeded_docs):
    row = await db.pool().fetchrow("SELECT centroid FROM documents WHERE id = $1", uuid.UUID(seeded_docs["a"]))
    assert row["centroid"] is not None
    norm = float(np.linalg.norm(row["centroid"].to_numpy().astype(np.float32)))
    assert norm == pytest.approx(1.0, abs=1e-3)

    before = row["centroid"].to_numpy().astype(np.float32).copy()
    await documents.update_document(ADMIN, seeded_docs["a"], "conteúdo mudou", content=FONTE_C + " outro")
    row = await db.pool().fetchrow("SELECT centroid FROM documents WHERE id = $1", uuid.UUID(seeded_docs["a"]))
    assert not np.allclose(before, row["centroid"].to_numpy().astype(np.float32))
    # restaura o conteúdo original para os demais testes
    await documents.update_document(ADMIN, seeded_docs["a"], "restaurando", content=FONTE_A)


async def test_graph_endpoint_documents_level(seeded_docs, admin_client):
    client = admin_client
    resp = await client.get(
        "/dash/api/graph", params={"collection": "grafos", "min_similarity": 0.5, "k": 3}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] == "documents"
    assert {n["id"] for n in data["nodes"]} == set(seeded_docs.values())
    assert {c["name"] for c in data["collections"]} == {"grafos"}
    assert next(n for n in data["nodes"] if n["id"] == seeded_docs["a"])["chunks"] == 1

    pairs = {frozenset((e["source"], e["target"])) for e in data["edges"]}
    ab = frozenset((seeded_docs["a"], seeded_docs["b"]))
    assert ab in pairs
    assert all(seeded_docs["c"] not in p for p in pairs)
    edge = next(e for e in data["edges"] if frozenset((e["source"], e["target"])) == ab)
    assert edge["similarity"] >= 0.7  # conteúdo quase idêntico → cosseno alto

    # threshold alto demais: nenhuma aresta passa
    resp = await client.get("/dash/api/graph", params={"collection": "grafos", "min_similarity": 0.95})
    assert resp.json()["edges"] == []


async def test_graph_endpoint_chunks_level(seeded_docs, admin_client):
    client = admin_client
    resp = await client.get(
        "/dash/api/graph",
        params={"level": "chunks", "collection": "grafos", "min_similarity": 0.5, "k": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] == "chunks"
    assert len(data["nodes"]) == 3  # 1 chunk por documento
    assert all(n["document_id"] in set(seeded_docs.values()) for n in data["nodes"])

    by_doc = {}
    for n in data["nodes"]:
        by_doc.setdefault(n["document_id"], []).append(n["id"])
    pairs = {frozenset((e["source"], e["target"])) for e in data["edges"]}
    assert frozenset((by_doc[seeded_docs["a"]][0], by_doc[seeded_docs["b"]][0])) in pairs
    assert all(by_doc[seeded_docs["c"]][0] not in p for p in pairs)


async def test_document_detail_endpoint(seeded_docs, admin_client):
    client = admin_client
    resp = await client.get(f"/dash/api/documents/{seeded_docs['a']}")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["title"] == "Alfa"
    assert doc["collection"] == "grafos"
    assert len(doc["chunks"]) == 1 and doc["chunks"][0]["chunk_index"] == 0
    assert doc["versions"][0]["version"] == doc["version"]

    resp = await client.get(f"/dash/api/documents/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------- fatia 3: gestão de contas


async def test_agents_endpoint_lists_aggregates(seeded_docs, admin_client):
    created = await agents.create_agent(ADMIN, slug="dashbot", name="Dash Bot", system_prompt="v1")
    items = (await admin_client.get("/dash/api/agents")).json()
    by_slug = {a["slug"]: a for a in items}
    assert by_slug["dashbot"]["active_keys"] == 1  # create_agent emite uma chave
    assert by_slug["dashbot"]["auto_apply_updates"] is False
    assert "config" not in by_slug["dashbot"]  # só expomos o que a tela usa

    # proposta pendente aparece no badge
    bot = await principal_for(created["api_key"])
    await agents.propose_agent_update(bot, "melhoria", system_prompt="v2")
    items = (await admin_client.get("/dash/api/agents")).json()
    assert next(a for a in items if a["slug"] == "dashbot")["proposals"] == 1


async def test_dash_autonomy_toggle(seeded_docs, admin_client):
    await agents.create_agent(ADMIN, slug="autonobot", name="Auto", system_prompt="v1")
    resp = await admin_client.post("/dash/api/agents/autonobot/autonomy", json={"auto_apply_updates": True})
    assert resp.status_code == 200 and resp.json()["auto_apply_updates"] is True
    row = await db.pool().fetchrow("SELECT config FROM agents WHERE slug = 'autonobot'")
    assert row["config"]["auto_apply_updates"] is True


async def test_keys_list_create_renew_revoke(seeded_docs, admin_client):
    resp = await admin_client.post(
        "/dash/api/keys", json={"label": "dash-test", "scopes": ["read", "write"]}
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["api_key"].startswith("kb_sk_")
    key_id = created["key_id"]

    keys = (await admin_client.get("/dash/api/keys")).json()
    mine = next(k for k in keys if k["id"] == key_id)
    assert mine["label"] == "dash-test" and mine["agent"] is None
    assert mine["scopes"] == ["read", "write"] and mine["revoked_at"] is None

    # a chave criada pela dash funciona na API pública
    principal = await principal_for(created["api_key"])
    assert principal.has("write")

    # renovar: revoga a antiga e emite uma nova com os mesmos escopos
    resp = await admin_client.post(f"/dash/api/keys/{key_id}/renew")
    assert resp.status_code == 200
    renewed = resp.json()
    assert renewed["api_key"] != created["api_key"]
    keys = (await admin_client.get("/dash/api/keys")).json()
    assert next(k for k in keys if k["id"] == key_id)["revoked_at"] is not None
    with pytest.raises(PermissionDenied):
        await principal_for(created["api_key"])
    assert (await principal_for(renewed["api_key"])).has("write")

    # chave já revogada não renova
    resp = await admin_client.post(f"/dash/api/keys/{key_id}/renew")
    assert resp.status_code == 400

    # revogar
    assert (await admin_client.post(f"/dash/api/keys/{renewed['key_id']}/revoke")).status_code == 200
    resp = await admin_client.post(f"/dash/api/keys/{renewed['key_id']}/revoke")
    assert resp.status_code == 404

    # chave ligada a agente inexistente → 404
    resp = await admin_client.post(
        "/dash/api/keys", json={"label": "x", "scopes": ["read"], "agent_slug": "nao-existe"}
    )
    assert resp.status_code == 404


async def test_users_management(seeded_docs, admin_client):
    resp = await admin_client.post(
        "/dash/api/users", json={"username": "novo-viewer", "password": "senha-12345", "role": "viewer"}
    )
    assert resp.status_code == 200
    uid = resp.json()["id"]

    users = (await admin_client.get("/dash/api/users")).json()
    assert any(u["username"] == "novo-viewer" and u["role"] == "viewer" for u in users)

    # troca de role
    resp = await admin_client.patch(f"/dash/api/users/{uid}", json={"role": "admin"})
    assert resp.status_code == 200 and resp.json()["role"] == "admin"

    # reset de senha derruba a sessão ativa do usuário
    client = await dash_client()
    resp = await client.post(
        "/dash/api/auth/login", json={"username": "novo-viewer", "password": "senha-12345"}
    )
    assert resp.status_code == 200
    resp = await admin_client.patch(f"/dash/api/users/{uid}", json={"password": "outra-senha-999"})
    assert resp.status_code == 200
    assert (await client.get("/dash/api/auth/me")).status_code == 401
    resp = await client.post(
        "/dash/api/auth/login", json={"username": "novo-viewer", "password": "outra-senha-999"}
    )
    assert resp.status_code == 200
    await client.aclose()

    # desativar bloqueia login
    resp = await admin_client.patch(f"/dash/api/users/{uid}", json={"disabled": True})
    assert resp.status_code == 200 and resp.json()["disabled_at"] is not None
    client = await dash_client()
    resp = await client.post(
        "/dash/api/auth/login", json={"username": "novo-viewer", "password": "outra-senha-999"}
    )
    assert resp.status_code == 401
    await client.aclose()

    # auto-desativação/rebaixamento é recusado
    me = (await admin_client.get("/dash/api/auth/me")).json()
    resp = await admin_client.patch(f"/dash/api/users/{me['id']}", json={"disabled": True})
    assert resp.status_code == 400
    resp = await admin_client.patch(f"/dash/api/users/{me['id']}", json={"role": "viewer"})
    assert resp.status_code == 400


async def test_viewer_restrictions(seeded_docs, admin_client):
    await dash_auth.create_user("um-viewer", "senha-viewer-1", "viewer")
    client = await dash_client()
    await client.post("/dash/api/auth/login", json={"username": "um-viewer", "password": "senha-viewer-1"})
    # viewer lê, mas não gerencia
    assert (await client.get("/dash/api/keys")).status_code == 200
    assert (await client.get("/dash/api/agents")).status_code == 200
    assert (await client.post("/dash/api/keys", json={"label": "x", "scopes": ["read"]})).status_code == 403
    assert (await client.get("/dash/api/users")).status_code == 403
    assert (
        await client.post("/dash/api/agents/autonobot/autonomy", json={"auto_apply_updates": True})
    ).status_code == 403
    await client.aclose()
