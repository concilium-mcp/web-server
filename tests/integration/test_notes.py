"""Tela Notas (plan-web-02): papel editor, CRUD de notas pela dash, conflito, histórico/restaurar, mover.

Coleções com prefixo "nt-" (o banco é compartilhado entre os módulos de integração).
"""

import asyncio

import httpx
import pytest

from mcp_rag_api.core import dash_auth
from mcp_rag_api.main import app


async def _client(username: str, role: str) -> httpx.AsyncClient:
    await dash_auth.create_user(username, "senha-segura-123", role)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://dash.test",
        headers={"X-Requested-With": "fetch"},  # exigido em mutações da dash (CSRF)
    )
    resp = await client.post("/dash/api/auth/login", json={"username": username, "password": "senha-segura-123"})
    assert resp.status_code == 200 and resp.json()["role"] == role
    return client


@pytest.fixture(scope="module")
async def editor():
    client = await _client("nt-editora", "editor")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def viewer():
    client = await _client("nt-leitora", "viewer")
    yield client
    await client.aclose()


async def test_editor_creates_collection_and_note_tree_has_no_content(editor):
    assert (await editor.post("/dash/api/collections", json={"name": "nt-comercial"})).status_code == 200
    assert (await editor.post("/dash/api/collections", json={"name": "nt-vazia"})).status_code == 200
    resp = await editor.post(
        "/dash/api/notes",
        json={"collection": "nt-comercial", "title": "Ata kickoff", "content": "# Ata\n\nzulu yankee", "tags": ["ata"]},
    )
    assert resp.status_code == 200 and resp.json()["created"]

    tree = (await editor.get("/dash/api/notes/tree")).json()
    cols = {c["name"]: c["documents"] for c in tree["collections"]}
    assert cols["nt-comercial"] == 1 and cols["nt-vazia"] == 0  # coleção vazia aparece
    note = next(n for n in tree["notes"] if n["title"] == "Ata kickoff")
    assert "content" not in note and note["tags"] == ["ata"] and note["version"] == 1


async def test_save_versions_conflict_and_restore(editor):
    tree = (await editor.get("/dash/api/notes/tree")).json()
    note_id = next(n["id"] for n in tree["notes"] if n["title"] == "Ata kickoff")

    saved = await editor.patch(f"/dash/api/notes/{note_id}", json={"base_version": 1, "content": "versão dois oscar"})
    assert saved.status_code == 200 and saved.json()["version"] == 2

    # alguém abriu a v1 e tenta salvar depois: 409 com a versão atual
    stale = await editor.patch(f"/dash/api/notes/{note_id}", json={"base_version": 1, "content": "perdido"})
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2

    versions = (await editor.get(f"/dash/api/notes/{note_id}/versions")).json()
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["change_note"] == "edição pela dashboard"
    v1 = (await editor.get(f"/dash/api/notes/{note_id}/versions/1")).json()
    assert "zulu yankee" in v1["content"]

    restored = (await editor.post(f"/dash/api/notes/{note_id}/restore/1")).json()
    assert restored["version"] == 3 and restored["restored_from"] == 1
    note = (await editor.get(f"/dash/api/notes/{note_id}")).json()
    assert "zulu yankee" in note["content"] and note["version"] == 3


async def test_simultaneous_saves_with_same_base_version_one_wins(editor):
    """Race real (plan-web-08): dois PATCH ao mesmo tempo com a mesma base_version — um 200 e um 409."""
    await editor.post("/dash/api/collections", json={"name": "nt-corrida"})  # tolera duplicata
    created = await editor.post(
        "/dash/api/notes",
        json={"collection": "nt-corrida", "title": "Corrida", "content": "linha de base"},
    )
    assert created.status_code == 200 and created.json()["created"]
    note_id = created.json()["document_id"]

    first, second = await asyncio.gather(
        editor.patch(f"/dash/api/notes/{note_id}", json={"base_version": 1, "content": "ramo alfa"}),
        editor.patch(f"/dash/api/notes/{note_id}", json={"base_version": 1, "content": "ramo beta"}),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    winner = second if loser is first else first
    assert loser.json()["detail"]["current_version"] == 2
    assert winner.json()["version"] == 2
    note = (await editor.get(f"/dash/api/notes/{note_id}")).json()
    assert note["version"] == 2  # um ramo sobreviveu, o outro nunca sobrescreveu


async def test_move_and_archive(editor):
    tree = (await editor.get("/dash/api/notes/tree")).json()
    note_id = next(n["id"] for n in tree["notes"] if n["title"] == "Ata kickoff")
    moved = (await editor.post(f"/dash/api/notes/{note_id}/move", json={"collection": "nt-vazia"})).json()
    assert moved["moved"] and (await editor.get(f"/dash/api/notes/{note_id}")).json()["collection"] == "nt-vazia"
    assert (
        await editor.post(f"/dash/api/notes/{note_id}/move", json={"collection": "nt-nao-existe"})
    ).status_code == 404

    assert (await editor.post(f"/dash/api/notes/{note_id}/archive", json={})).status_code == 200
    tree = (await editor.get("/dash/api/notes/tree")).json()
    assert not any(n["id"] == note_id for n in tree["notes"])


async def test_duplicate_warning_then_force(editor):
    body = {"collection": "nt-comercial", "title": "Playbook", "content": "objeção preço resposta valor"}
    assert (await editor.post("/dash/api/notes", json=body)).json()["created"]
    dup = (await editor.post("/dash/api/notes", json=body)).json()  # mesmo texto de novo
    assert dup["created"] is False and dup["reason"] == "duplicate_suspected"
    forced = (await editor.post("/dash/api/notes", json={**body, "force": True})).json()
    assert forced["created"]


async def test_viewer_reads_but_cannot_write(viewer):
    tree = (await viewer.get("/dash/api/notes/tree")).json()
    note_id = tree["notes"][0]["id"]
    assert (await viewer.get(f"/dash/api/notes/{note_id}")).status_code == 200
    assert (
        await viewer.post("/dash/api/notes", json={"collection": "nt-comercial", "title": "x", "content": "y"})
    ).status_code == 403
    assert (
        await viewer.patch(f"/dash/api/notes/{note_id}", json={"base_version": 1, "content": "z"})
    ).status_code == 403
    assert (await viewer.post("/dash/api/collections", json={"name": "nt-viewer"})).status_code == 403
    assert (await viewer.post(f"/dash/api/notes/{note_id}/archive", json={})).status_code == 403


async def test_editor_is_not_admin(editor):
    assert (await editor.get("/dash/api/users")).status_code == 403
    assert (await editor.post("/dash/api/keys", json={"label": "x", "scopes": ["read"]})).status_code == 403


def test_editor_role_helpers():
    assert dash_auth.DashUser("1", "e", "editor").can_edit and not dash_auth.DashUser("1", "e", "editor").is_admin
    assert not dash_auth.DashUser("1", "v", "viewer").can_edit


async def test_related_endpoint(editor):
    tree = (await editor.get("/dash/api/notes/tree")).json()
    note_id = tree["notes"][0]["id"]
    rel = (await editor.get(f"/dash/api/notes/{note_id}/related")).json()
    assert set(rel) == {"document", "links", "backlinks", "semantic"}


async def test_insights_endpoint(editor, viewer):
    data = (await viewer.get("/dash/api/insights", params={"days": 7})).json()
    assert data["days"] == 7 and len(data["activity"]) == 7  # um ponto por dia, com zeros
    assert data["totals"]["documents"] >= 1 and "pending_links" in data["totals"]
    today = data["activity"][-1]
    assert today["edits"] >= 1 and today["created"] >= 1  # o módulo criou/editou notas hoje
    assert any(c["name"] == "nt-comercial" for c in data["by_collection"])
    assert any(c["actor"] == "dash:nt-editora" for c in data["contributors"])
    assert {"documents", "with_tags", "with_links", "stale"} <= set(data["health"])
    assert data["created"]["current"] >= 1 and len(data["recent"]) >= 1
    assert (await viewer.get("/dash/api/insights", params={"days": 0})).status_code == 422
