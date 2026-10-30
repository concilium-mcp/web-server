"""Pastas pela API pública e pelo MCP (plan-web-16): caminho "A/B/C", criar, colocar e mover notas.

Coleções com prefixo "fa-" (o banco é compartilhado entre os módulos de integração).
"""

import httpx
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import documents, folders
from mcp_rag_api.main import app
from mcp_rag_api.mcp_server import mcp
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import KBError, create_api_key


async def _key(scopes: list[str]) -> str:
    async with db.pool().acquire() as conn:
        return (await create_api_key(conn, label=f"fa-{'-'.join(scopes)}", scopes=scopes))["api_key"]


@pytest.fixture(scope="module")
async def writer():
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {await _key(['read', 'write'])}"},
    )
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def reader():
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {await _key(['read'])}"},
    )
    yield client
    await client.aclose()


async def test_create_path_is_idempotent_and_case_insensitive(writer, reader):
    await documents.create_collection(ADMIN, "fa-clientes")
    first = await writer.post("/folders", json={"collection": "fa-clientes", "path": "Acme/Reuniões/2026"})
    assert first.status_code == 200, first.text
    assert first.json()["created"] == ["Acme", "Acme/Reuniões", "Acme/Reuniões/2026"]
    again = await writer.post("/folders", json={"collection": "fa-clientes", "path": " acme / REUNIÕES /2026/Out "})
    assert again.json()["created"] == ["Acme/Reuniões/2026/Out"]  # só o nível novo
    assert again.json()["path"] == "Acme/Reuniões/2026/Out"  # caminho com os nomes como estão gravados

    listed = (await reader.get("/folders", params={"collection": "fa-clientes"})).json()
    assert {f["path"] for f in listed} == {"Acme", "Acme/Reuniões", "Acme/Reuniões/2026", "Acme/Reuniões/2026/Out"}

    assert (await reader.post("/folders", json={"collection": "fa-clientes", "path": "X"})).status_code == 403
    assert (await writer.post("/folders", json={"collection": "fa-clientes", "path": " / "})).status_code == 400


async def test_add_document_in_folder_and_get_shows_path(writer):
    resp = await writer.post(
        "/documents",
        json={
            "collection": "fa-clientes",
            "title": "Ata Acme",
            "content": "Ata da reunião de outubro com a Acme.",
            "folder_path": "Acme/Reuniões/2026",
        },
    )
    assert resp.status_code == 200 and resp.json()["folder_path"] == "Acme/Reuniões/2026"
    doc = (await writer.get(f"/documents/{resp.json()['document_id']}")).json()
    assert doc["folder_path"] == "Acme/Reuniões/2026" and doc["folder_id"]

    root = await writer.post(
        "/documents", json={"collection": "fa-clientes", "title": "Raiz", "content": "Nota sem pasta nenhuma aqui."}
    )
    assert (await writer.get(f"/documents/{root.json()['document_id']}")).json()["folder_path"] is None


async def test_move_between_collections_and_to_root(writer):
    await documents.create_collection(ADMIN, "fa-arquivo")
    doc_id = (
        await writer.post(
            "/documents",
            json={
                "collection": "fa-clientes",
                "title": "Contrato Acme",
                "content": "Contrato assinado com a Acme.",
                "folder_path": "Acme",
            },
        )
    ).json()["document_id"]
    moved = await writer.post(
        f"/documents/{doc_id}/move", json={"collection": "fa-arquivo", "folder_path": "2026/Contratos"}
    )
    assert moved.status_code == 200 and moved.json()["folder_path"] == "2026/Contratos"
    doc = (await writer.get(f"/documents/{doc_id}")).json()
    assert doc["collection"] == "fa-arquivo" and doc["folder_path"] == "2026/Contratos"

    to_root = await writer.post(f"/documents/{doc_id}/move", json={"collection": "fa-arquivo"})
    assert to_root.json()["folder_path"] is None


async def test_upsert_keeps_note_in_folder():
    first = await folders.upsert_document_in_folder(
        ADMIN, "Sync/CRM", collection="fa-clientes", external_id="crm-9", title="Contato", content="Ramal 200."
    )
    assert first["folder_path"] == "Sync/CRM"
    second = await folders.upsert_document_in_folder(
        ADMIN, "Sync/CRM", collection="fa-clientes", external_id="crm-9", title="Contato", content="Ramal 300."
    )
    assert second["folder_path"] == "Sync/CRM" and second["version"] == 2


async def test_slash_in_name_is_rejected_and_invalid_path_creates_nothing():
    with pytest.raises(KBError):
        folders.validate_folder_name("A/B")
    with pytest.raises(KBError):
        await folders.add_document_in_folder(
            ADMIN, "  /  ", collection="fa-clientes", title="Nada", content="Não deve ser criado."
        )
    listed = await documents.list_documents(ADMIN, "fa-clientes", None, "active", 100, 0)
    assert all(d["title"] != "Nada" for d in listed)


async def test_mcp_tools_exposed():
    names = {t.name for t in await mcp.list_tools()}
    assert {"list_folders", "create_folder", "move_document"} <= names


async def test_list_documents_filters_by_folder_path(writer, reader):
    await documents.create_collection(ADMIN, "fa-filtro")
    created = {}
    for title, path in [
        ("Planos e preços", "Atendimento/Plataforma"),
        ("Quando transferir", "Atendimento"),
        ("Roadmap interno", "Produto"),
        ("Nota na raiz", None),
    ]:
        body = {"collection": "fa-filtro", "title": title, "content": f"Conteúdo exclusivo de {title} para o filtro."}
        if path:
            body["folder_path"] = path
        resp = await writer.post("/documents", json=body)
        assert resp.json()["created"], resp.text
        created[title] = resp.json()["document_id"]

    def titles(resp):
        assert resp.status_code == 200, resp.text
        return {d["title"]: d["folder_path"] for d in resp.json()}

    # pasta + subpastas, sem diferenciar maiúsculas
    tree = titles(await reader.get("/documents", params={"collection": "fa-filtro", "folder_path": "atendimento"}))
    assert tree == {"Planos e preços": "Atendimento/Plataforma", "Quando transferir": "Atendimento"}

    only = titles(
        await reader.get(
            "/documents", params={"collection": "fa-filtro", "folder_path": "Atendimento", "recursive": "false"}
        )
    )
    assert only == {"Quando transferir": "Atendimento"}

    # sem filtro: todas, com folder_path (null = raiz)
    everything = titles(await reader.get("/documents", params={"collection": "fa-filtro"}))
    assert everything["Nota na raiz"] is None and everything["Roadmap interno"] == "Produto"

    missing = await reader.get("/documents", params={"collection": "fa-filtro", "folder_path": "Nao/Existe"})
    assert missing.status_code == 404
    no_collection = await reader.get("/documents", params={"folder_path": "Atendimento"})
    assert no_collection.status_code == 400

    # MCP: mesmo filtro
    result = await folders.list_documents_in_folder(
        ADMIN, "fa-filtro", folder_path="Atendimento/Plataforma", recursive=False
    )
    assert [d["id"] for d in result] == [created["Planos e preços"]]
