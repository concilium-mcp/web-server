"""Quadros Kanban nas pastas (plan-web-37a): quadros, colunas, cartões, posse, checklist e auditoria."""

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import agents, boards, brief, documents, folders
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import KBError, NotFound, PermissionDenied, Principal

COLLECTION = "boards-w37"
OTHER = "boards-w37-outra"
S1 = boards.Origin.of("Claude Code 2.1", "sessao-1")
S2 = boards.Origin.of("Claude Code 2.1", "sessao-2")


def agent(slug: str, collections: tuple[str, ...] = (COLLECTION,)) -> Principal:
    return Principal(
        actor=f"agent:{slug}",
        scopes=frozenset({"read", "write"}),
        agent_slug=slug,
        allowed_collections=collections,
    )


PERSON = Principal(actor="dash:ana", scopes=frozenset({"read", "write"}))
READER = Principal(actor="key:leitor", scopes=frozenset({"read"}))


@pytest.fixture(scope="module", autouse=True)
async def setup():
    await documents.create_collection(ADMIN, COLLECTION)
    await documents.create_collection(ADMIN, OTHER)


def _name() -> str:
    return f"Quadro {uuid.uuid4().hex[:6]}"


async def _board(folder_path: str | None = "Vendas", **kwargs) -> dict:
    return await boards.create_board(ADMIN, COLLECTION, _name(), folder_path, **kwargs)


async def _events(card_id: str) -> list[dict]:
    return (await boards.card_log(ADMIN, card_id))["events"]


async def test_board_with_default_columns_and_unique_name():
    board = await boards.create_board(ADMIN, COLLECTION, "Prospecção 2027", "Vendas")
    assert board["folder_path"] == "Vendas"
    assert [(c["name"], c["role"]) for c in board["columns"]] == list(boards.DEFAULT_COLUMNS)
    with pytest.raises(KBError, match="Já existe"):
        await boards.create_board(ADMIN, COLLECTION, "prospecção 2027", "Vendas")
    # o mesmo nome em outra pasta pode
    other = await boards.create_board(ADMIN, COLLECTION, "Prospecção 2027", "Botucatu")
    assert other["id"] != board["id"]
    found = await boards.get_board(ADMIN, collection=COLLECTION, folder_path="vendas", name="PROSPECÇÃO 2027")
    assert found["id"] == board["id"]


async def test_free_columns_guess_roles():
    board = await _board(columns=["Entrada", "A fazer", "Fazendo", "Revisão", "Concluído", "Pausado"])
    roles = {c["name"]: c["role"] for c in board["columns"]}
    assert roles == {
        "Entrada": "backlog",
        "A fazer": "ready",
        "Fazendo": "doing",
        "Revisão": "review",
        "Concluído": "done",
        "Pausado": None,
    }


async def test_list_boards_parent_folder_sees_children():
    await boards.create_board(ADMIN, COLLECTION, "Implantação", "Brian/Botucatu")
    await boards.create_board(ADMIN, COLLECTION, "Licitações", "Brian/Vendas")
    names = {b["name"] for b in await boards.list_boards(ADMIN, COLLECTION, "Brian")}
    assert names == {"Implantação", "Licitações"}
    only = await boards.list_boards(ADMIN, COLLECTION, "Brian/Vendas")
    assert [b["folder_path"] for b in only] == ["Brian/Vendas"]


async def test_columns_create_update_delete():
    board = await _board(columns=["Fila", "Feito"])
    col = await boards.create_column(ADMIN, board["id"], "Testando", role="review", position=1)
    with pytest.raises(KBError, match="papel 'review'"):
        await boards.create_column(ADMIN, board["id"], "Outra", role="review")
    names = [c["name"] for c in (await boards.get_board(ADMIN, board["id"]))["columns"]]
    assert names == ["Fila", "Testando", "Feito"]
    await boards.update_column(ADMIN, col["id"], name="Em teste", position=0)
    card = await boards.create_card(ADMIN, board["id"], "Cartão na coluna", column="Em teste")
    with pytest.raises(KBError, match="move_cards_to"):
        await boards.delete_column(ADMIN, col["id"])
    result = await boards.delete_column(ADMIN, col["id"], move_cards_to="Fila")
    assert result["moved_cards"] == 1
    assert (await boards.get_card(ADMIN, card["id"]))["column"] == "Fila"
    kinds = [e["kind"] for e in (await boards.board_log(ADMIN, board["id"]))["events"]]
    assert {"column.create", "column.update", "column.delete", "board.create"} <= set(kinds)


async def test_card_default_column_checklist_and_progress():
    board = await _board()
    card = await boards.create_card(ADMIN, board["id"], "Corrigir webhook", checklist=["ler", "corrigir", "testar"])
    assert card["column"] == "Pronto para pegar" and card["progress"] == "0/3" and card["next_item"] == "ler"
    r = await boards.check_item(ADMIN, card["id"], text="corrig")
    assert r["progress"] == "1/3" and r["next_item"] == "ler"
    first = card["items"][0]["id"]
    await boards.check_item(ADMIN, card["id"], item_id=first)
    await boards.check_item(ADMIN, card["id"], item_id=first, done=False)
    await boards.add_items(ADMIN, card["id"], ["abrir MR"])
    full = await boards.get_card(ADMIN, card["id"])
    assert full["progress"] == "1/4"
    kinds = [e["kind"] for e in await _events(card["id"])]
    assert kinds[:4] == ["item.add", "item.uncheck", "item.check", "item.check"]


async def test_claim_two_sessions_and_lease():
    board = await _board()
    card = await boards.create_card(ADMIN, board["id"], "Tarefa disputada", checklist=["a", "b"], repo="web-server")
    a, b = agent("dev-a"), agent("dev-b")
    started = await boards.start_card(a, card["id"], origin=S1)
    assert started["column"] == "Em andamento" and started["claim"] == "active"
    assert started["suggested"] == {
        "worktree": "worktrees/web-server-tarefa-disputada",
        "branch": "feat/tarefa-disputada",
    }
    # outro agente não pega nem mexe
    with pytest.raises(PermissionDenied, match="já está com agent:dev-a"):
        await boards.start_card(b, card["id"], origin=S2)
    with pytest.raises(PermissionDenied):
        await boards.check_item(b, card["id"], text="a")
    # o mesmo agente em outra sessão precisa de force
    with pytest.raises(KBError, match="outra sessão sua"):
        await boards.start_card(a, card["id"], origin=S2)
    resumed = await boards.start_card(a, card["id"], force=True, origin=S2)
    assert resumed["session"] == "sessao-2"
    # pessoa passa por cima da posse
    await boards.update_card(PERSON, card["id"], description="ajuste humano")
    # posse vencida: vira "parado", gera evento e outro agente retoma
    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE cards SET lease_until = now() - interval '1 minute' WHERE id = $1", uuid.UUID(card["id"])
        )
    stale = await boards.get_card(ADMIN, card["id"])
    assert stale["claim"] == "stale"
    await boards.get_board(ADMIN, board["id"])  # segunda leitura não duplica o evento
    kinds = [e["kind"] for e in await _events(card["id"])]
    assert kinds.count("claim.expired") == 1
    taken = await boards.start_card(b, card["id"], origin=S1)
    assert taken["claimed_by"] == "agent:dev-b"
    assert (await _events(card["id"]))[0]["kind"] == "claim.takeover"


async def test_writes_renew_lease():
    board = await _board()
    card = await boards.create_card(ADMIN, board["id"], "Renovar", checklist=["x"])
    a = agent("dev-renew")
    await boards.start_card(a, card["id"], origin=S1)
    async with db.pool().acquire() as conn:
        soon = datetime.now(UTC) + timedelta(minutes=10)
        await conn.execute("UPDATE cards SET lease_until = $2 WHERE id = $1", uuid.UUID(card["id"]), soon)
    await boards.log_card(a, card["id"], "commit abc123", "commit", origin=S1)
    full = await boards.get_card(ADMIN, card["id"])
    assert datetime.fromisoformat(full["lease_until"]) > datetime.now(UTC) + timedelta(minutes=100)


async def test_flow_release_submit_finish_and_my_work():
    board = await _board()
    a = agent("dev-flow")
    card = await boards.create_card(ADMIN, board["id"], "Fluxo completo", checklist=["um", "dois"], repo="web-server")
    await boards.start_card(a, card["id"], origin=S1)
    await boards.update_card(a, card["id"], worktree="worktrees/web-server-fluxo", branch="feat/fluxo", origin=S1)
    await boards.check_item(a, card["id"], text="um", origin=S1)
    work = await boards.my_work(a, session="sessao-1", repo="web-server")
    assert [c["title"] for c in work["holding"]] == ["Fluxo completo"]
    assert work["holding"][0]["this_session"] and work["holding"][0]["next_item"] == "dois"
    assert (await boards.my_work(a, repo="outro-repo"))["holding"] == []

    released = await boards.release_card(a, card["id"], "falta o passo dois", origin=S1)
    assert released["column"] == "Pronto para pegar"
    assert (await boards.get_card(ADMIN, card["id"]))["claim"] is None

    await boards.start_card(a, card["id"], origin=S1)
    await boards.block_card(a, card["id"], "esperando credencial", origin=S1)
    assert (await boards.get_card(ADMIN, card["id"]))["blocked"] == "esperando credencial"
    await boards.block_card(a, card["id"], "", origin=S1)
    submitted = await boards.submit_card(a, card["id"], "MR aberta", "https://gitlab.example/mr/1", origin=S1)
    assert submitted["role"] == "review"
    work = await boards.my_work(a)
    assert [c["title"] for c in work["review"]] == ["Fluxo completo"]

    plan = await documents.add_document(ADMIN, COLLECTION, "Plano do fluxo", "# Plano\n\nTexto.", force=True)
    await boards.link_card_document(ADMIN, card["id"], plan["document_id"])
    done = await boards.finish_card(PERSON, card["id"], "mergeado", append_to_document=plan["document_id"])
    assert done["role"] == "done"
    full = await boards.get_card(ADMIN, card["id"])
    assert full["done_at"] and full["documents"][0]["title"] == "Plano do fluxo"
    assert "cartão concluído: Fluxo completo" in (await documents.get_document(ADMIN, plan["document_id"]))["content"]
    assert [c["title"] for c in await boards.cards_for_document(ADMIN, plan["document_id"])] == ["Fluxo completo"]
    assert (await boards.my_work(a))["review"] == []


async def test_events_have_actor_client_session_and_diff():
    board = await _board()
    a = agent("dev-audit")
    card = await boards.create_card(ADMIN, board["id"], "Auditar")
    await boards.start_card(a, card["id"], origin=S1)
    await boards.update_card(a, card["id"], branch="feat/x", mr_url="https://mr/2", origin=S1)
    event = (await _events(card["id"]))[0]
    assert event["kind"] == "card.update" and event["actor"] == "agent:dev-audit"
    assert event["actor_kind"] == "agent" and event["client"] == "Claude Code 2.1" and event["session_id"] == "sessao-1"
    assert event["changes"] == {"branch": [None, "feat/x"], "mr_url": [None, "https://mr/2"]}
    # filtros do log
    log = await boards.board_log(ADMIN, board["id"], kind="claim")
    assert [e["kind"] for e in log["events"]] == ["claim.start"]
    assert (await boards.board_log(ADMIN, board["id"], actor="agent:dev-audit"))["events"]
    page = await boards.board_log(ADMIN, board["id"], limit=1)
    assert page["next_before_id"] is not None
    older = await boards.board_log(ADMIN, board["id"], limit=1, before_id=page["next_before_id"])
    assert older["events"][0]["id"] < page["events"][0]["id"]


async def test_board_events_are_append_only():
    board = await _board()
    async with db.pool().acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="só de acréscimo"):
            await conn.execute("UPDATE board_events SET note = 'x' WHERE board_id = $1", uuid.UUID(board["id"]))
        with pytest.raises(asyncpg.RaiseError, match="só de acréscimo"):
            await conn.execute("DELETE FROM board_events WHERE board_id = $1", uuid.UUID(board["id"]))
        with pytest.raises(asyncpg.RaiseError, match="só de acréscimo"):
            await conn.execute("TRUNCATE board_events")


async def test_permissions_by_collection_and_scope():
    board = await _board()
    card = await boards.create_card(ADMIN, board["id"], "Restrito")
    outsider = agent("dev-fora", (OTHER,))
    with pytest.raises(PermissionDenied):
        await boards.get_board(outsider, board["id"])
    with pytest.raises(PermissionDenied):
        await boards.start_card(outsider, card["id"])
    assert all(b["collection"] == OTHER for b in await boards.list_boards(outsider))
    with pytest.raises(PermissionDenied):
        await boards.create_card(READER, board["id"], "não pode")
    assert (await boards.get_board(READER, board["id"]))["id"] == board["id"]


async def test_moving_and_deleting_folder_keeps_boards():
    f = await folders.ensure_folder_path(ADMIN, COLLECTION, "Mover/Origem")
    board = await boards.create_board(ADMIN, COLLECTION, "Viajante", "Mover/Origem")
    clash = await boards.create_board(ADMIN, COLLECTION, "Viajante", "Mover")
    # pasta movida para outra coleção leva o quadro
    await folders.move_folder(ADMIN, f["id"], OTHER)
    moved = await boards.get_board(ADMIN, board["id"])
    assert moved["collection"] == OTHER and moved["folder_path"] == "Origem"
    await folders.move_folder(
        ADMIN, f["id"], COLLECTION, (await folders.ensure_folder_path(ADMIN, COLLECTION, "Mover"))["id"]
    )
    # excluir a pasta sobe o quadro; o nome repetido lá ganha o nome da pasta
    result = await folders.delete_folder(ADMIN, f["id"])
    assert result["moved_boards"] == 1
    lifted = await boards.get_board(ADMIN, board["id"])
    assert lifted["folder_path"] == "Mover" and lifted["name"] == "Viajante (Origem)"
    assert (await boards.get_board(ADMIN, clash["id"]))["name"] == "Viajante"


async def test_archive_board_and_card():
    board = await _board()
    card = await boards.create_card(ADMIN, board["id"], "Arquivar")
    await boards.update_card(ADMIN, card["id"], archived=True)
    assert (await boards.get_board(ADMIN, board["id"]))["columns"][1]["count"] == 0
    with pytest.raises(KBError, match="arquivado"):
        await boards.update_card(ADMIN, card["id"], title="x")
    await boards.update_card(ADMIN, card["id"], archived=False)
    await boards.update_board(ADMIN, board["id"], archived=True)
    with pytest.raises(KBError, match="arquivado"):
        await boards.create_card(ADMIN, board["id"], "não")
    assert board["id"] not in {b["id"] for b in await boards.list_boards(ADMIN, COLLECTION)}
    # o nome fica livre para um quadro novo
    await boards.create_board(ADMIN, COLLECTION, board["name"], "Vendas")


async def test_search_cards_and_brief():
    board = await _board()
    slug = f"brief37-{uuid.uuid4().hex[:5]}"
    await agents.create_agent(ADMIN, slug, "Agente", "Você é um teste.", allowed_collections=[COLLECTION])
    me, other = agent(slug), agent("dev-vizinho")
    mine = await boards.create_card(ADMIN, board["id"], "Integrar pagamento Pix", description="webhook do banco")
    theirs = await boards.create_card(ADMIN, board["id"], "Refatorar relatórios")
    await boards.start_card(me, mine["id"], repo="pronfi", origin=S1)
    await boards.update_card(me, mine["id"], worktree="worktrees/pronfi-pix", origin=S1)
    await boards.start_card(other, theirs["id"], origin=S2)
    found = await boards.search_cards(ADMIN, "pagamentos pix", COLLECTION)
    assert [c["title"] for c in found] == ["Integrar pagamento Pix"]
    assert await boards.search_cards(ADMIN, "webhook", COLLECTION)
    data = await brief.get_brief(ADMIN, COLLECTION, slug, repo="pronfi", work_session="sessao-1")
    assert "## Seus cartões" in data["text"] and "Integrar pagamento Pix" in data["text"]
    assert "worktree worktrees/pronfi-pix" in data["text"] and "desta sessão" in data["text"]
    assert "## Em andamento agora" in data["text"] and "Refatorar relatórios" in data["text"]


async def test_not_found_and_validation():
    board = await _board()
    with pytest.raises(NotFound):
        await boards.get_card(ADMIN, str(uuid.uuid4()))
    with pytest.raises(KBError, match="Prazo inválido"):
        await boards.create_card(ADMIN, board["id"], "x", due="amanhã")
    with pytest.raises(KBError, match="kind inválido"):
        card = await boards.create_card(ADMIN, board["id"], "y")
        await boards.log_card(ADMIN, card["id"], "z", "fofoca")
    with pytest.raises(NotFound, match="Coluna"):
        await boards.create_card(ADMIN, board["id"], "w", column="Inexistente")


# ---------------------------------------------------------------- REST e MCP


async def test_rest_flow_with_session_header():
    import httpx

    from mcp_rag_api.main import app
    from mcp_rag_api.security import create_api_key

    async with db.pool().acquire() as conn:
        key = (await create_api_key(conn, label="boards-rest", scopes=["read", "write"]))["api_key"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {key}", "User-Agent": "script-teste/1.0"},
    ) as client:
        r = await client.post("/boards", json={"collection": COLLECTION, "name": _name(), "folder_path": "Rest"})
        assert r.status_code == 200, r.text
        board_id = r.json()["id"]
        r = await client.post(f"/boards/{board_id}/cards", json={"title": "Pela API", "checklist": ["um"]})
        card_id = r.json()["id"]
        r = await client.post(f"/cards/{card_id}/start", json={}, headers={"X-Concilium-Session": "rest-1"})
        assert r.json()["claim"] == "active" and r.json()["session"] == "rest-1"
        r = await client.post(f"/cards/{card_id}/check", json={"text": "um", "session": "rest-1"})
        assert r.json()["progress"] == "1/1"
        assert (await client.get("/my-work", params={"session": "rest-1"})).json()["holding"][0]["this_session"]
        assert (await client.get("/cards/search", params={"q": "API"})).json()[0]["id"] == card_id
        events = (await client.get(f"/cards/{card_id}/log")).json()["events"]
        assert events[0]["client"] == "script-teste/1.0" and events[0]["session_id"] == "rest-1"
        r = await client.get(f"/boards/{board_id}/log", params={"kind": "card"})
        assert [e["kind"] for e in r.json()["events"]] == ["card.create"]


async def test_mcp_tools_visible_by_scope():
    from mcp_rag_api.mcp_server import tool_visible

    for name in ("list_boards", "get_board", "my_work", "board_log", "search_cards", "get_card"):
        assert tool_visible(name, READER)
    for name in ("create_card", "start_card", "finish_card", "create_board", "set_column"):
        assert not tool_visible(name, READER) and tool_visible(name, agent("x"))


async def test_lean_responses_are_much_smaller():
    """Tools de escrita do MCP devolvem o essencial (plan-web-38 mostrou ~1,5 mil tokens por chamada)."""
    import json

    from mcp_rag_api.core.tokens import estimate_tokens

    board = await _board()
    desc = "Contexto do cartão com bastante texto para pesar na resposta. " * 20
    card = await boards.create_card(ADMIN, board["id"], "Cartão pesado", desc, ["um", "dois", "três"])
    a = agent("dev-lean")
    full = await boards.start_card(a, card["id"], origin=S1)
    for i in range(5):
        await boards.log_card(a, card["id"], f"commit {i} com uma mensagem razoável", "commit", origin=S1)
    full = await boards.update_card(a, card["id"], branch="feat/lean", origin=S1)
    lean = boards.lean_card(full)
    assert lean["id"] == card["id"] and lean["branch"] == "feat/lean" and lean["progress"] == "0/3"
    assert "description" not in lean and "log" not in lean and "items" not in lean
    assert estimate_tokens(json.dumps(lean)) * 5 < estimate_tokens(json.dumps(full))
    with_items = boards.lean_card(full, items=True)
    assert [i["text"] for i in with_items["items"]] == ["um", "dois", "três"]
    slim = boards.lean_board(await boards.get_board(ADMIN, board["id"]))
    assert all("cards" not in c for c in slim["columns"]) and slim["columns"][2]["count"] == 1
