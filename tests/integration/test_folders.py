"""Pastas aninhadas (plan-web-14): árvore infinita dentro da coleção, mover, renomear, excluir sem perder nada.

Coleções com prefixo "fd-" (o banco é compartilhado entre os módulos de integração).
"""

import httpx
import pytest

from mcp_rag_api.core import dash_auth, documents
from mcp_rag_api.main import app
from mcp_rag_api.security import Principal


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
async def editor():
    client = await _client("fd-editora", "editor")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def viewer():
    client = await _client("fd-leitora", "viewer")
    yield client
    await client.aclose()


async def _folder(client, collection: str, name: str, parent_id: str | None = None) -> str:
    resp = await client.post("/dash/api/folders", json={"collection": collection, "name": name, "parent_id": parent_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _note(client, collection: str, title: str, folder_id: str | None = None) -> str:
    resp = await client.post(
        "/dash/api/notes",
        json={
            "collection": collection,
            "title": title,
            "content": f"# {title}\n\n{title} conteúdo único",
            "folder_id": folder_id,
        },
    )
    assert resp.status_code == 200 and resp.json()["created"], resp.text
    return resp.json()["document_id"]


async def _tree(client) -> dict:
    return (await client.get("/dash/api/notes/tree")).json()


async def test_deep_nesting_and_tree(editor):
    await editor.post("/dash/api/collections", json={"name": "fd-botucatu"})
    parent = None
    ids = []
    for depth in range(8):  # "infinito" na prática: sem limite de profundidade
        parent = await _folder(editor, "fd-botucatu", f"Nível {depth}", parent)
        ids.append(parent)
    note_id = await _note(editor, "fd-botucatu", "Secretaria de Educação", ids[-1])

    tree = await _tree(editor)
    by_id = {f["id"]: f for f in tree["folders"]}
    assert by_id[ids[0]]["parent_id"] is None and by_id[ids[0]]["collection"] == "fd-botucatu"
    assert all(by_id[ids[i]]["parent_id"] == ids[i - 1] for i in range(1, 8))
    note = next(n for n in tree["notes"] if n["id"] == note_id)
    assert note["folder_id"] == ids[-1] and note["collection"] == "fd-botucatu"


async def test_viewer_cannot_create_and_names_are_validated(editor, viewer):
    resp = await viewer.post("/dash/api/folders", json={"collection": "fd-botucatu", "name": "x"})
    assert resp.status_code == 403
    resp = await editor.post("/dash/api/folders", json={"collection": "fd-botucatu", "name": "   "})
    assert resp.status_code == 400
    resp = await editor.post("/dash/api/folders", json={"collection": "fd-nao-existe", "name": "x"})
    assert resp.status_code == 404
    # nome livre (acento, aspas, travessão) — é só rótulo, exibido escapado; "/" é o separador de caminho
    fid = await _folder(editor, "fd-botucatu", '  Projetos — 2026 "Q4"  ')
    assert next(f for f in (await _tree(editor))["folders"] if f["id"] == fid)["name"] == 'Projetos — 2026 "Q4"'
    resp = await editor.post("/dash/api/folders", json={"collection": "fd-botucatu", "name": "A/B"})
    assert resp.status_code == 400


async def test_rename_and_cycle_protection(editor):
    a = await _folder(editor, "fd-botucatu", "A")
    b = await _folder(editor, "fd-botucatu", "B", a)
    c = await _folder(editor, "fd-botucatu", "C", b)
    assert (await editor.patch(f"/dash/api/folders/{a}", json={"name": "A renomeada"})).status_code == 200

    for target in (a, c):  # para dentro de si mesma ou de um descendente
        resp = await editor.post(f"/dash/api/folders/{a}/move", json={"collection": "fd-botucatu", "parent_id": target})
        assert resp.status_code == 400

    # C sobe para a raiz da coleção
    resp = await editor.post(f"/dash/api/folders/{c}/move", json={"collection": "fd-botucatu", "parent_id": None})
    assert resp.status_code == 200
    by_id = {f["id"]: f for f in (await _tree(editor))["folders"]}
    assert by_id[a]["name"] == "A renomeada" and by_id[c]["parent_id"] is None


async def test_move_folder_to_other_collection_takes_subtree(editor):
    await editor.post("/dash/api/collections", json={"name": "fd-origem"})
    await editor.post("/dash/api/collections", json={"name": "fd-destino"})
    top = await _folder(editor, "fd-origem", "Cliente X")
    sub = await _folder(editor, "fd-origem", "Reuniões", top)
    n1 = await _note(editor, "fd-origem", "Ata cliente X", sub)
    n2 = await _note(editor, "fd-origem", "Contrato cliente X", top)
    dest = await _folder(editor, "fd-destino", "Clientes")

    resp = await editor.post(f"/dash/api/folders/{top}/move", json={"collection": "fd-destino", "parent_id": dest})
    assert resp.status_code == 200, resp.text
    tree = await _tree(editor)
    by_id = {f["id"]: f for f in tree["folders"]}
    assert by_id[top]["collection"] == "fd-destino" and by_id[top]["parent_id"] == dest
    assert by_id[sub]["collection"] == "fd-destino" and by_id[sub]["parent_id"] == top
    notes = {n["id"]: n for n in tree["notes"]}
    assert notes[n1]["collection"] == "fd-destino" and notes[n1]["folder_id"] == sub
    assert notes[n2]["collection"] == "fd-destino" and notes[n2]["folder_id"] == top


async def test_move_note_between_folders_and_core_move_resets_folder(editor):
    await editor.post("/dash/api/collections", json={"name": "fd-notas"})
    await editor.post("/dash/api/collections", json={"name": "fd-outra"})
    f1 = await _folder(editor, "fd-notas", "Pasta 1")
    f2 = await _folder(editor, "fd-notas", "Pasta 2", f1)
    other = await _folder(editor, "fd-outra", "Lá")
    note_id = await _note(editor, "fd-notas", "Nota que anda", f1)

    resp = await editor.post(f"/dash/api/notes/{note_id}/move", json={"collection": "fd-notas", "folder_id": f2})
    assert resp.status_code == 200 and resp.json()["folder_id"] == f2
    # pasta de outra coleção é recusada
    resp = await editor.post(f"/dash/api/notes/{note_id}/move", json={"collection": "fd-notas", "folder_id": other})
    assert resp.status_code == 400
    resp = await editor.post(f"/dash/api/notes/{note_id}/move", json={"collection": "fd-outra", "folder_id": other})
    assert resp.status_code == 200
    assert next(n for n in (await _tree(editor))["notes"] if n["id"] == note_id)["folder_id"] == other

    # move pelo core (MCP/REST não conhecem pastas): a nota cai na raiz da coleção nova, sem quebrar a FK
    p = Principal(actor="test", scopes=frozenset({"write"}))
    await documents.move_document(p, note_id, "fd-notas")
    note = next(n for n in (await _tree(editor))["notes"] if n["id"] == note_id)
    assert note["collection"] == "fd-notas" and note["folder_id"] is None


async def test_create_note_in_folder_of_other_collection_is_rejected_before_creating(editor):
    other = await _folder(editor, "fd-outra", "Alheia")
    resp = await editor.post(
        "/dash/api/notes",
        json={"collection": "fd-notas", "title": "Não deve existir", "content": "nada", "folder_id": other},
    )
    assert resp.status_code == 400
    assert not any(n["title"] == "Não deve existir" for n in (await _tree(editor))["notes"])


async def test_delete_folder_lifts_contents_one_level(editor):
    await editor.post("/dash/api/collections", json={"name": "fd-excluir"})
    top = await _folder(editor, "fd-excluir", "Topo")
    mid = await _folder(editor, "fd-excluir", "Meio", top)
    low = await _folder(editor, "fd-excluir", "Baixo", mid)
    note_id = await _note(editor, "fd-excluir", "Nota do meio", mid)

    resp = await editor.delete(f"/dash/api/folders/{mid}")
    assert resp.status_code == 200 and resp.json()["moved_folders"] == 1 and resp.json()["moved_notes"] == 1
    tree = await _tree(editor)
    by_id = {f["id"]: f for f in tree["folders"]}
    assert mid not in by_id and by_id[low]["parent_id"] == top
    assert next(n for n in tree["notes"] if n["id"] == note_id)["folder_id"] == top

    # excluir a de cima: tudo vai para a raiz da coleção
    assert (await editor.delete(f"/dash/api/folders/{top}")).status_code == 200
    tree = await _tree(editor)
    assert {f["id"]: f for f in tree["folders"]}[low]["parent_id"] is None
    assert next(n for n in tree["notes"] if n["id"] == note_id)["folder_id"] is None
