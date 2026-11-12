"""Quadros na dashboard (plan-web-37b): as mesmas ações do core/boards, com o usuário da sessão.

Leitor vê; editor e admin mexem (como nas notas). A origem no log é o cliente "dashboard". A posse de um
agente não bloqueia uma pessoa (core: _guard), e "Liberar" força a liberação.
"""

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .core import boards
from .core.dash_auth import DashUser
from .dashboard_api import current_user, require_csrf_header, require_editor
from .security import Principal

router = APIRouter(
    prefix="/dash/api", tags=["dashboard"], dependencies=[Depends(require_csrf_header), Depends(current_user)]
)
DASH = boards.Origin.of("dashboard", None)


def _p(user: DashUser) -> Principal:
    # pessoa da dash enxerga todas as coleções (como em Notas); escreve só com papel de editor
    return Principal(actor=f"dash:{user.username}", scopes=frozenset({"write" if user.can_edit else "read"}))


class BoardIn(BaseModel):
    collection: str
    name: str = Field(max_length=200)
    folder_path: str | None = None
    description: str | None = None
    columns: list[str | dict[str, Any]] | None = None


class BoardPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    folder_path: str | None = None
    archived: bool | None = None


class ColumnIn(BaseModel):
    name: str
    role: str | None = None
    position: int | None = None


class ColumnPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    position: int | None = None
    wip_limit: int | None = None


class CardIn(BaseModel):
    title: str
    column: str | None = None
    description: str | None = None
    checklist: list[str] | None = None


class CardPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    due: str | None = None
    labels: list[str] | None = None
    repo: str | None = None
    worktree: str | None = None
    branch: str | None = None
    mr_url: str | None = None
    ci_status: str | None = None
    column: str | None = None
    position: int | None = None
    assignee: str | None = None
    archived: bool | None = None
    blocked_reason: str | None = None


class ItemsIn(BaseModel):
    items: list[str]


class CheckIn(BaseModel):
    item_id: int
    done: bool = True


class ItemPatch(BaseModel):
    text: str | None = None
    delete: bool = False


class ReleaseIn(BaseModel):
    next_step: str = "liberado pela dashboard"


class LinkIn(BaseModel):
    document_id: str
    unlink: bool = False


@router.get("/boards")
async def list_boards(
    collection: str | None = None, folder_path: str | None = None, user: DashUser = Depends(current_user)
) -> list[dict]:
    return await boards.list_boards(_p(user), collection or None, folder_path or None)


@router.post("/boards")
async def create_board(body: BoardIn, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.create_board(
        _p(user), body.collection, body.name, body.folder_path, body.description, body.columns, origin=DASH
    )


@router.get("/boards/{board_id}")
async def get_board(board_id: str, include_done: bool = False, user: DashUser = Depends(current_user)) -> dict:
    return await boards.get_board(_p(user), board_id, include_done=include_done)


@router.patch("/boards/{board_id}")
async def update_board(board_id: str, body: BoardPatch, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.update_board(_p(user), board_id, origin=DASH, **body.model_dump())


def _log_params(card_id: str | None, actor: str | None, kind: str | None, since: str | None, until: str | None) -> dict:
    return {
        "card_id": card_id or None,
        "actor": actor or None,
        "kind": kind or None,
        "since": since or None,
        "until": until or None,
    }


@router.get("/boards/{board_id}/log")
async def board_log(
    board_id: str,
    card_id: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = None,
    user: DashUser = Depends(current_user),
) -> dict:
    return await boards.board_log(
        _p(user), board_id, limit=limit, before_id=before_id, **_log_params(card_id, actor, kind, since, until)
    )


@router.get("/boards/{board_id}/log.csv")
async def board_log_csv(
    board_id: str,
    card_id: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
    user: DashUser = Depends(current_user),
) -> Response:
    """Log inteiro (com os filtros) em CSV para auditoria: pagina por dentro até o fim."""
    p = _p(user)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "quando", "quem", "tipo_de_ator", "cliente", "sessao", "acao", "cartao", "mudancas", "nota"])
    before: int | None = None
    while True:
        page = await boards.board_log(
            p, board_id, limit=500, before_id=before, **_log_params(card_id, actor, kind, since, until)
        )
        for e in page["events"]:
            changes = "; ".join(f"{k}: {v[0]} → {v[1]}" for k, v in (e.get("changes") or {}).items())
            writer.writerow(
                [
                    e["id"],
                    e["created_at"],
                    e["actor"],
                    e["actor_kind"],
                    e.get("client", ""),
                    e.get("session_id", ""),
                    e["kind"],
                    e.get("card_id", ""),
                    changes,
                    e.get("note", ""),
                ]
            )
        before = page["next_before_id"]
        if before is None:
            break
    return Response(
        content="﻿" + out.getvalue(),  # BOM: o Excel abre os acentos certos
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="quadro-{board_id[:8]}-log.csv"'},
    )


@router.post("/boards/{board_id}/columns")
async def create_column(board_id: str, body: ColumnIn, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.create_column(_p(user), board_id, body.name, body.role or None, body.position, origin=DASH)


@router.patch("/columns/{column_id}")
async def update_column(column_id: str, body: ColumnPatch, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.update_column(_p(user), column_id, origin=DASH, **body.model_dump())


@router.delete("/columns/{column_id}")
async def delete_column(
    column_id: str, move_cards_to: str | None = None, user: DashUser = Depends(require_editor)
) -> dict:
    return await boards.delete_column(_p(user), column_id, move_cards_to or None, origin=DASH)


@router.post("/boards/{board_id}/cards")
async def create_card(board_id: str, body: CardIn, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.create_card(
        _p(user), board_id, body.title, body.description, body.checklist, column=body.column, origin=DASH
    )


@router.get("/cards/{card_id}")
async def get_card(card_id: str, user: DashUser = Depends(current_user)) -> dict:
    return await boards.get_card(_p(user), card_id, events=30)


@router.patch("/cards/{card_id}")
async def update_card(card_id: str, body: CardPatch, user: DashUser = Depends(require_editor)) -> dict:
    data = body.model_dump()
    blocked = data.pop("blocked_reason")
    p = _p(user)
    if blocked is not None:
        await boards.block_card(p, card_id, blocked, origin=DASH)
    return await boards.update_card(p, card_id, origin=DASH, **data)


@router.post("/cards/{card_id}/items")
async def add_items(card_id: str, body: ItemsIn, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.add_items(_p(user), card_id, body.items, origin=DASH)


@router.post("/cards/{card_id}/check")
async def check_item(card_id: str, body: CheckIn, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.check_item(_p(user), card_id, body.item_id, None, body.done, origin=DASH)


@router.patch("/cards/{card_id}/items/{item_id}")
async def edit_item(card_id: str, item_id: int, body: ItemPatch, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.edit_item(_p(user), card_id, item_id, body.text, body.delete, origin=DASH)


@router.post("/cards/{card_id}/release")
async def release_card(card_id: str, body: ReleaseIn, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.release_card(_p(user), card_id, body.next_step, force=True, origin=DASH)


@router.post("/cards/{card_id}/documents")
async def link_document(card_id: str, body: LinkIn, user: DashUser = Depends(require_editor)) -> dict:
    return await boards.link_card_document(_p(user), card_id, body.document_id, body.unlink, origin=DASH)


@router.get("/notes/{document_id}/cards")
async def note_cards(document_id: str, user: DashUser = Depends(current_user)) -> list[dict]:
    return await boards.cards_for_document(_p(user), document_id)
