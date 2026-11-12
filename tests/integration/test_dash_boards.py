"""Quadros pela dashboard (plan-web-37b): leitor vê, editor mexe, pessoa passa por cima da posse do agente."""

import httpx
import pytest

from mcp_rag_api.core import boards, dash_auth, documents
from mcp_rag_api.main import app
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import Principal

COLLECTION = "dash-boards-37b"
H = {"X-Requested-With": "fetch"}


async def _login(username: str, role: str) -> httpx.AsyncClient:
    await dash_auth.create_user(username, "senha-bem-comprida-37b", role)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dash.test", headers=H)
    r = await client.post("/dash/api/auth/login", json={"username": username, "password": "senha-bem-comprida-37b"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture(scope="module")
async def clients():
    await documents.create_collection(ADMIN, COLLECTION)
    editor = await _login("editor-37b", "editor")
    leitor = await _login("leitor-37b", "viewer")
    yield editor, leitor
    await editor.aclose()
    await leitor.aclose()


async def test_editor_flow_and_viewer_read_only(clients):
    editor, leitor = clients
    r = await editor.post(
        "/dash/api/boards", json={"collection": COLLECTION, "name": "Implantação", "folder_path": "Botucatu"}
    )
    assert r.status_code == 200, r.text
    board = r.json()
    ready = board["columns"][1]["id"]
    doing = board["columns"][2]["id"]
    card = (
        await editor.post(
            f"/dash/api/boards/{board['id']}/cards", json={"title": "Treinar a recepção", "column": ready}
        )
    ).json()
    # leitor vê, mas não mexe
    assert (await leitor.get(f"/dash/api/boards/{board['id']}")).status_code == 200
    assert (await leitor.post(f"/dash/api/boards/{board['id']}/cards", json={"title": "x"})).status_code == 403
    # mover (arrastar) e checklist
    r = await editor.patch(
        f"/dash/api/cards/{card['id']}",
        json={"column": doing, "position": 0, "labels": ["piloto"], "blocked_reason": "sem sala"},
    )
    assert r.status_code == 200, r.text
    assert (
        r.json()["column"] == "Em andamento" and r.json()["blocked"] == "sem sala" and r.json()["labels"] == ["piloto"]
    )
    full = (await editor.post(f"/dash/api/cards/{card['id']}/items", json={"items": ["agenda", "convênios"]})).json()
    item = full["items"][0]["id"]
    assert (await editor.post(f"/dash/api/cards/{card['id']}/check", json={"item_id": item})).json()[
        "progress"
    ] == "1/2"
    edited = (
        await editor.patch(f"/dash/api/cards/{card['id']}/items/{item}", json={"text": "agenda da semana"})
    ).json()
    assert edited["items"][0]["text"] == "agenda da semana"
    after = (
        await editor.patch(f"/dash/api/cards/{card['id']}/items/{full['items'][1]['id']}", json={"delete": True})
    ).json()
    assert after["progress"] == "1/1"
    # colunas: criar, renomear, apagar movendo os cartões
    col = (await editor.post(f"/dash/api/boards/{board['id']}/columns", json={"name": "Homologação"})).json()
    assert (
        await editor.patch(f"/dash/api/columns/{col['id']}", json={"name": "Homologar", "role": ""})
    ).status_code == 200
    assert (await editor.delete(f"/dash/api/columns/{col['id']}")).json()["deleted"]
    # log com quem fez pela dash, filtro por parte do nome e CSV
    log = (await editor.get(f"/dash/api/boards/{board['id']}/log", params={"actor": "editor-37b"})).json()
    kinds = {e["kind"] for e in log["events"]}
    assert {
        "board.create",
        "card.create",
        "card.update",
        "card.block",
        "item.check",
        "item.edit",
        "item.delete",
    } <= kinds
    assert all(e["client"] == "dashboard" for e in log["events"])
    csv = await editor.get(f"/dash/api/boards/{board['id']}/log.csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "quando,quem" in csv.text and "item.edit" in csv.text and "agenda → agenda da semana" in csv.text
    listed = (await leitor.get("/dash/api/boards")).json()
    assert any(b["id"] == board["id"] and b["folder_path"] == "Botucatu" for b in listed)


async def test_person_overrides_agent_claim_and_releases(clients):
    editor, _ = clients
    board = await boards.create_board(ADMIN, COLLECTION, "Suporte", "Botucatu")
    card = await boards.create_card(ADMIN, board["id"], "Responder chamados")
    agente = Principal(actor="agent:suporte-37b", scopes=frozenset({"read", "write"}), agent_slug="suporte-37b")
    await boards.start_card(agente, card["id"], origin=boards.Origin.of("Claude Code", "s1"))
    # a pessoa edita mesmo com a posse do agente valendo, e "Liberar" força
    assert (
        await editor.patch(f"/dash/api/cards/{card['id']}", json={"description": "prioridade alta"})
    ).status_code == 200
    r = await editor.post(f"/dash/api/cards/{card['id']}/release", json={"next_step": "voltar amanhã"})
    assert r.status_code == 200, r.text
    got = (await editor.get(f"/dash/api/cards/{card['id']}")).json()
    assert got["claim"] is None and got["column"] == "Pronto para pegar"
    assert got["log"][0]["kind"] == "claim.force_release"


async def test_note_shows_linked_cards(clients):
    editor, _ = clients
    board = await boards.create_board(ADMIN, COLLECTION, "Com notas")
    card = await boards.create_card(ADMIN, board["id"], "Ligado ao plano")
    note = await documents.add_document(ADMIN, COLLECTION, "Plano 37b", "conteúdo do plano", force=True)
    assert (
        await editor.post(f"/dash/api/cards/{card['id']}/documents", json={"document_id": note["document_id"]})
    ).json()["linked"]
    cards = (await editor.get(f"/dash/api/notes/{note['document_id']}/cards")).json()
    assert [c["title"] for c in cards] == ["Ligado ao plano"]
