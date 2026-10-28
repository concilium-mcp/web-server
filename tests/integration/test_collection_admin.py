"""Renomear e excluir a pasta principal (coleção) — plan-web-18.

Coleções com prefixo "ca-" (o banco é compartilhado entre os módulos de integração).
"""

import httpx
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import agents, documents, folders
from mcp_rag_api.main import app
from mcp_rag_api.mcp_server import mcp
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import KBError, PermissionDenied, Principal, create_api_key

WRITER = Principal(actor="ca-writer", scopes=frozenset({"read", "write"}))


async def _key(scopes: list[str]) -> str:
    async with db.pool().acquire() as conn:
        return (await create_api_key(conn, label=f"ca-{'-'.join(scopes)}", scopes=scopes))["api_key"]


@pytest.fixture(scope="module")
async def admin_client():
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {await _key(['admin'])}"},
    )
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def writer_client():
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {await _key(['read', 'write'])}"},
    )
    yield client
    await client.aclose()


async def _collection_exists(name: str) -> bool:
    return bool(await db.pool().fetchval("SELECT 1 FROM collections WHERE name = $1", name))


async def test_delete_requires_admin(writer_client):
    await documents.create_collection(ADMIN, "ca-so-admin")
    with pytest.raises(PermissionDenied):
        await folders.delete_collection(WRITER, "ca-so-admin")
    assert (await writer_client.delete("/collections/ca-so-admin")).status_code == 403
    assert await _collection_exists("ca-so-admin")


async def test_delete_refuses_active_notes(admin_client):
    await documents.create_collection(ADMIN, "ca-com-notas")
    await documents.add_document(ADMIN, "ca-com-notas", "Ativa", "Nota ativa que impede a exclusão.")
    resp = await admin_client.delete("/collections/ca-com-notas")
    assert resp.status_code == 400 and "nota(s) ativa(s)" in resp.json()["detail"]
    assert await _collection_exists("ca-com-notas")


async def test_delete_takes_archived_notes_and_folders(admin_client):
    await documents.create_collection(ADMIN, "ca-limpar")
    doc = await documents.add_document(ADMIN, "ca-limpar", "Velha", "Nota antiga que já foi arquivada.")
    await documents.archive_document(ADMIN, doc["document_id"], "teste")
    await folders.ensure_folder_path(ADMIN, "ca-limpar", "A/B")

    resp = await admin_client.delete("/collections/ca-limpar")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True, "name": "ca-limpar", "archived_notes_deleted": 1, "folders_deleted": 2}
    assert not await _collection_exists("ca-limpar")
    assert not await db.pool().fetchval("SELECT 1 FROM documents WHERE id = $1", doc["document_id"])
    action = await db.pool().fetchval(
        "SELECT action FROM audit_log WHERE action = 'collection.delete' AND details->>'name' = 'ca-limpar'"
    )
    assert action == "collection.delete"


async def test_delete_refuses_when_agent_is_restricted_to_it():
    # tirar o nome da lista do agente faria "só esta coleção" virar "todas" — por isso recusa
    await documents.create_collection(ADMIN, "ca-do-agente")
    await agents.create_agent(ADMIN, "ca-agente", "Agente CA", "teste", allowed_collections=["ca-do-agente"])
    with pytest.raises(KBError, match="ca-agente"):
        await folders.delete_collection(ADMIN, "ca-do-agente")

    await agents.archive_agent(ADMIN, "ca-agente", "teste")  # agente arquivado não bloqueia
    assert (await folders.delete_collection(ADMIN, "ca-do-agente"))["deleted"] is True


async def test_rename_moves_restricted_access_to_new_name(admin_client):
    await documents.create_collection(ADMIN, "ca-antigo")
    doc = await documents.add_document(ADMIN, "ca-antigo", "Nota", "Nota que acompanha a coleção renomeada.")
    await agents.create_agent(ADMIN, "ca-renomeia", "Agente R", "teste", allowed_collections=["ca-antigo", "ca-outra"])

    resp = await admin_client.patch("/collections/ca-antigo", json={"name": "ca-novo"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "ca-novo" and resp.json()["agents_updated"] == 1

    allowed = await db.pool().fetchval("SELECT allowed_collections FROM agents WHERE slug = 'ca-renomeia'")
    assert list(allowed) == ["ca-novo", "ca-outra"]
    assert (await documents.get_document(ADMIN, doc["document_id"]))["collection"] == "ca-novo"
    assert not await _collection_exists("ca-antigo")


async def test_rename_validates_and_refuses_existing_name(admin_client, writer_client):
    await documents.create_collection(ADMIN, "ca-um")
    await documents.create_collection(ADMIN, "ca-dois")
    assert (await admin_client.patch("/collections/ca-um", json={"name": "ca-dois"})).status_code == 400
    assert (await admin_client.patch("/collections/ca-um", json={"name": "/barra"})).status_code == 400
    assert (await admin_client.patch("/collections/ca-inexistente", json={"name": "ca-x"})).status_code == 404
    assert (await writer_client.patch("/collections/ca-um", json={"name": "ca-tres"})).status_code == 403
    same = await admin_client.patch("/collections/ca-um", json={"name": "ca-um"})
    assert same.status_code == 200 and same.json()["renamed"] is False


async def test_mcp_exposes_rename_and_delete():
    names = {t.name for t in await mcp.list_tools()}
    assert {"rename_collection", "delete_collection"} <= names
