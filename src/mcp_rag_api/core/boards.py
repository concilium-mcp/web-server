"""Quadros Kanban nas pastas (plan-web-37a) — só no web-server.

O quadro é o projeto e mora numa pasta da coleção (folder_id NULL = raiz); uma pasta tem quantos
quadros quiser. Dentro dele, colunas livres com um papel opcional (backlog, ready, doing, review,
done): é o papel que as ações dos agentes usam para mover o cartão.

Posse (claim): `start_card` marca o cartão como da sessão por `card_lease_minutes` (padrão 2 h),
renovados a cada escrita dela. Enquanto vale, só quem segura (ou uma pessoa da dash/admin) mexe.
Vencida, o cartão fica "parado" e outra sessão pode retomar. O MCP roda sem sessão (stateless):
a sessão é um texto que o cliente informa (o plugin do Claude Code passa o id da conversa).

Toda escrita grava um evento em `board_events` (só de acréscimo; o banco recusa UPDATE/DELETE),
com quem, de qual cliente e sessão, o quê e, nas edições, antes → depois.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from ..config import get_settings
from ..db import pool, record, records
from ..security import KBError, NotFound, PermissionDenied, Principal
from . import documents, folders
from .documents import parse_uuid

ROLES = ("backlog", "ready", "doing", "review", "done")
DEFAULT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Ideias", "backlog"),
    ("Pronto para pegar", "ready"),
    ("Em andamento", "doing"),
    ("Aguardando revisão", "review"),
    ("Feito", "done"),
)
# palavras do nome digitado que sugerem o papel da coluna (quando o papel não vem explícito)
_ROLE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("done", ("feito", "conclu", "done", "pronto!", "entregue", "finaliz")),
    ("review", ("revis", "review", "aprova", "homolog")),
    ("doing", ("andamento", "fazendo", "doing", "progress", "execu")),
    ("ready", ("pronto", "ready", "a fazer", "to do", "todo", "próximo", "proximo")),
    ("backlog", ("ideia", "backlog", "entrada", "inbox")),
)
LOG_KINDS = ("note", "commit", "mr", "ci", "decision", "blocker")
BOARD_NAME_MAX = 120
COLUMN_NAME_MAX = 80
CARD_TITLE_MAX = 300
ITEM_TEXT_MAX = 500
DONE_CARDS_SHOWN = 10
EVENTS_MAX = 500
_ZERO = uuid.UUID(int=0)
# campos de texto do cartão que o update_card aceita direto ("" limpa)
_TEXT_FIELDS = ("description", "repo", "worktree", "branch", "mr_url", "ci_status")


@dataclass(frozen=True)
class Origin:
    """De onde veio a ação (vai para o log): nome do cliente (Claude Code, claude.ai, dash…) e sessão."""

    client: str | None = None
    session: str | None = None

    @classmethod
    def of(cls, client: str | None, session: str | None) -> "Origin":
        def clean(value: str | None, limit: int) -> str | None:
            value = " ".join((value or "").split())[:limit]
            return value or None

        return cls(clean(client, 120), clean(session, 200))


NO_ORIGIN = Origin()


def actor_kind(actor: str) -> str:
    prefix = actor.split(":", 1)[0]
    return {"dash": "user", "agent": "agent", "key": "key", "integration": "integration"}.get(prefix, "system")


def _is_person(p: Principal) -> bool:
    """Pessoa da dash (ou pelo Claude dela) e admin passam por cima da posse de um agente."""
    return p.actor.startswith("dash:") or p.has("admin")


def _clean(text: str | None, label: str, limit: int, *, required: bool = True) -> str | None:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        if required:
            raise KBError(f"{label} não pode ficar vazio.")
        return None
    if len(cleaned) > limit:
        raise KBError(f"{label} passa de {limit} caracteres.")
    return cleaned


def _role(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    role = value.strip().lower()
    if role not in ROLES:
        raise KBError(f"Papel de coluna inválido: '{value}'. Use um de {', '.join(ROLES)} (ou vazio).")
    return role


def guess_role(name: str) -> str | None:
    lowered = name.lower()
    for role, hints in _ROLE_HINTS:
        if any(h in lowered for h in hints):
            return role
    return None


def _columns_spec(columns: list[Any] | None) -> list[tuple[str, str | None]]:
    """Lista de colunas do quadro novo: nomes (papel deduzido do nome) ou {name, role}."""
    if not columns:
        return list(DEFAULT_COLUMNS)
    spec: list[tuple[str, str | None]] = []
    used: set[str] = set()
    for col in columns:
        if isinstance(col, str):
            name, role, explicit = col, None, False
        elif isinstance(col, dict):
            name, role, explicit = str(col.get("name") or ""), _role(col.get("role")), True
        else:
            raise KBError("Cada coluna é um nome ou {name, role}.")
        cleaned = _clean(name, "Nome da coluna", COLUMN_NAME_MAX)
        assert cleaned is not None
        if not explicit:
            role = guess_role(cleaned)
        if role in used:
            if explicit:
                raise KBError(f"O papel '{role}' aparece em duas colunas; cada papel vale para uma só.")
            role = None
        if role:
            used.add(role)
        spec.append((cleaned, role))
    if len(spec) > 30:
        raise KBError("Um quadro tem no máximo 30 colunas.")
    return spec


def _parse_due(value: date | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as e:
        raise KBError(f"Prazo inválido: '{value}'. Use AAAA-MM-DD.") from e


def _labels(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        label = _clean(v, "Etiqueta", 40, required=False)
        if label and label not in out:
            out.append(label)
    return out[:20]


def slugify(text: str, limit: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:limit].rstrip("-") or "cartao"


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def _lease() -> timedelta:
    return timedelta(minutes=get_settings().card_lease_minutes)


# ---------------------------------------------------------------- eventos (auditoria)


async def _event(
    conn: asyncpg.Connection,
    p: Principal | None,
    origin: Origin,
    board_id: uuid.UUID,
    card_id: uuid.UUID | None,
    kind: str,
    changes: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    actor = p.actor if p is not None else "system"
    await conn.execute(
        "INSERT INTO board_events (board_id, card_id, actor, actor_kind, client, session_id, kind, changes, note) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        board_id,
        card_id,
        actor,
        actor_kind(actor),
        origin.client,
        origin.session,
        kind,
        {k: [_jsonable(a), _jsonable(b)] for k, (a, b) in changes.items()} if changes else None,
        note,
    )


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    return {k: (before.get(k), v) for k, v in after.items() if before.get(k) != v}


async def _mark_stale(
    conn: asyncpg.Connection, *, board_ids: list[uuid.UUID] | None = None, actor: str | None = None
) -> None:
    """Posse vencida gera um evento (uma vez), detectada na leitura."""
    rows = await conn.fetch(
        """
        UPDATE cards SET stale_logged_at = now()
        WHERE claimed_by IS NOT NULL AND lease_until < now() AND stale_logged_at IS NULL AND archived_at IS NULL
          AND ($1::uuid[] IS NULL OR board_id = ANY($1)) AND ($2::text IS NULL OR claimed_by = $2)
        RETURNING id, board_id, claimed_by, claim_session, lease_until
        """,
        board_ids,
        actor,
    )
    for r in rows:
        await _event(
            conn,
            None,
            Origin(session=r["claim_session"]),
            r["board_id"],
            r["id"],
            "claim.expired",
            note=f"Posse de {r['claimed_by']} venceu em {r['lease_until'].isoformat(timespec='minutes')}.",
        )


# ---------------------------------------------------------------- carregamento e permissão


async def _load_board(conn: asyncpg.Connection, p: Principal, board_id: str | uuid.UUID, *, lock: bool = False):
    bid = board_id if isinstance(board_id, uuid.UUID) else parse_uuid(board_id, "board_id")
    row = await conn.fetchrow(
        "SELECT b.*, c.name AS collection FROM boards b JOIN collections c ON c.id = b.collection_id "
        "WHERE b.id = $1" + (" FOR UPDATE OF b" if lock else ""),
        bid,
    )
    if row is None:
        raise NotFound(f"Quadro {board_id} não encontrado.")
    p.require_collection(row["collection"])
    return dict(row)


def _writable_board(board: dict) -> None:
    if board["archived_at"] is not None:
        raise KBError(f"O quadro '{board['name']}' está arquivado.")


async def _load_card(conn: asyncpg.Connection, p: Principal, card_id: str, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(
        """
        SELECT k.*, b.name AS board_name, b.archived_at AS board_archived_at, b.collection_id,
               c.name AS collection, bc.name AS column_name, bc.role
        FROM cards k JOIN boards b ON b.id = k.board_id JOIN collections c ON c.id = b.collection_id
        JOIN board_columns bc ON bc.id = k.column_id
        WHERE k.id = $1
        """
        + (" FOR UPDATE OF k" if lock else ""),
        parse_uuid(card_id, "card_id"),
    )
    if row is None:
        raise NotFound(f"Cartão {card_id} não encontrado.")
    p.require_collection(row["collection"])
    return dict(row)


def _writable_card(card: dict) -> None:
    if card["board_archived_at"] is not None:
        raise KBError(f"O quadro '{card['board_name']}' está arquivado.")
    if card["archived_at"] is not None:
        raise KBError("O cartão está arquivado.")


def claim_state(card: dict, now: datetime | None = None) -> str | None:
    if not card.get("claimed_by") or card.get("lease_until") is None:
        return None
    return "active" if card["lease_until"] > (now or _now()) else "stale"


def _guard(p: Principal, card: dict) -> None:
    """Enquanto a posse vale, só quem segura (ou uma pessoa/admin) mexe no cartão."""
    if claim_state(card) == "active" and card["claimed_by"] != p.actor and not _is_person(p):
        until = card["lease_until"].isoformat(timespec="minutes")
        raise PermissionDenied(
            f"O cartão está com {card['claimed_by']} até {until}. Escolha outro cartão ou espere a posse vencer."
        )


async def _renew(conn: asyncpg.Connection, p: Principal, card_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE cards SET lease_until = now() + $3::interval, stale_logged_at = NULL "
        "WHERE id = $1 AND claimed_by = $2 AND lease_until IS NOT NULL",
        card_id,
        p.actor,
        _lease(),
    )


async def _touch(conn: asyncpg.Connection, card_id: uuid.UUID, actor: str) -> None:
    await conn.execute("UPDATE cards SET updated_at = now(), updated_by = $2 WHERE id = $1", card_id, actor)


async def _columns(conn: asyncpg.Connection, board_id: uuid.UUID) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, name, role, position, wip_limit FROM board_columns WHERE board_id = $1 "
        "ORDER BY position, created_at",
        board_id,
    )
    return [dict(r) for r in rows]


async def _resolve_column(conn: asyncpg.Connection, board_id: uuid.UUID, ref: str) -> dict:
    """Coluna por id, por papel (backlog/ready/doing/review/done) ou pelo nome (sem diferenciar maiúsculas)."""
    cols = await _columns(conn, board_id)
    ref_clean = ref.strip()
    try:
        cid = uuid.UUID(ref_clean)
    except ValueError:
        cid = None
    for col in cols:
        if cid is not None and col["id"] == cid:
            return col
    for col in cols:
        if col["role"] == ref_clean.lower():
            return col
    for col in cols:
        if col["name"].lower() == ref_clean.lower():
            return col
    names = ", ".join(c["name"] for c in cols)
    raise NotFound(f"Coluna '{ref}' não existe neste quadro (colunas: {names}).")


async def _column_by_role(conn: asyncpg.Connection, board_id: uuid.UUID, *roles: str) -> dict | None:
    cols = await _columns(conn, board_id)
    for role in roles:
        for col in cols:
            if col["role"] == role:
                return col
    return None


async def _next_position(conn: asyncpg.Connection, column_id: uuid.UUID) -> float:
    current = await conn.fetchval("SELECT max(position) FROM cards WHERE column_id = $1", column_id)
    return float(current or 0) + 1


async def _folder_paths(p: Principal, collection: str) -> dict[uuid.UUID, str]:
    return {uuid.UUID(f["id"]): f["path"] for f in await folders.list_folders(p, collection)}


# ---------------------------------------------------------------- formato das respostas


_CARD_SELECT = """
    SELECT k.id, k.board_id, k.column_id, bc.name AS column_name, bc.role, k.title, k.position, k.labels,
           k.due_date, k.claimed_by, k.claim_session, k.claimed_at, k.lease_until, k.blocked_reason, k.repo,
           k.worktree, k.branch, k.mr_url, k.ci_status, k.done_at, k.archived_at, k.updated_at,
           a.slug AS assignee_agent, u.username AS assignee_user,
           (SELECT count(*) FROM card_items i WHERE i.card_id = k.id) AS items_total,
           (SELECT count(*) FROM card_items i WHERE i.card_id = k.id AND i.done) AS items_done,
           (SELECT text FROM card_items i WHERE i.card_id = k.id AND NOT i.done ORDER BY position, id LIMIT 1)
             AS next_item
    FROM cards k JOIN board_columns bc ON bc.id = k.column_id
    LEFT JOIN agents a ON a.id = k.assignee_agent_id LEFT JOIN dash_users u ON u.id = k.assignee_user_id
"""


def _card_summary(row: asyncpg.Record | dict, now: datetime | None = None) -> dict:
    r = dict(row)
    total, done = r.pop("items_total", 0) or 0, r.pop("items_done", 0) or 0
    state = claim_state(r, now)
    out = {
        "id": str(r["id"]),
        "title": r["title"],
        "column": r["column_name"],
        "role": r["role"],
        "progress": f"{done}/{total}" if total else None,
        "next_item": r.get("next_item") if total else None,
        "labels": r["labels"] or None,
        "due": r["due_date"].isoformat() if r.get("due_date") else None,
        "assignee": r.get("assignee_agent") and f"agent:{r['assignee_agent']}" or r.get("assignee_user"),
        "claimed_by": r["claimed_by"] if state else None,
        "claim": state,
        "session": r["claim_session"] if state else None,
        "lease_until": r["lease_until"].isoformat(timespec="minutes") if state else None,
        "blocked": r["blocked_reason"],
        "repo": r["repo"],
        "worktree": r["worktree"],
        "branch": r["branch"],
        "mr_url": r["mr_url"],
        "ci": r["ci_status"],
        "done_at": r["done_at"].isoformat(timespec="minutes") if r.get("done_at") else None,
        "updated_at": r["updated_at"].isoformat(timespec="minutes"),
    }
    if r.get("archived_at"):
        out["archived"] = True
    if state is None and r.get("claimed_by"):
        out["worked_by"] = r["claimed_by"]  # mandou para revisão ou concluiu
    return out


def _event_out(row: asyncpg.Record, *, drop: tuple[str, ...] = ("board_id",)) -> dict:
    """Evento sem nulos e sem os ids que a resposta já traz no topo (menos tokens para o agente)."""
    r = record(row)
    assert r is not None
    return {k: v for k, v in r.items() if v is not None and k not in drop}


# ---------------------------------------------------------------- quadros


async def list_boards(
    p: Principal, collection: str | None = None, folder_path: str | None = None, include_archived: bool = False
) -> list[dict]:
    """Quadros visíveis; com folder_path, os da pasta e das subpastas (a pasta mãe vê as filhas)."""
    p.require("read")
    if folder_path and not collection:
        raise KBError("Informe a coleção junto com folder_path.")
    async with pool().acquire() as conn:
        folder_ids: list[uuid.UUID] | None = None
        if collection:
            cid = await folders._collection(conn, p, collection)
            if folder_path:
                fid, _, _ = await folders._resolve_path(conn, p, cid, collection, folder_path, create=False)
                folder_ids = await folders.folder_subtree_ids(conn, p, str(fid))
        rows = await conn.fetch(
            """
            SELECT b.id, c.name AS collection, b.folder_id, b.name, b.description, b.owner, b.updated_at,
                   b.archived_at,
                   count(k.id) FILTER (WHERE bc.role IS DISTINCT FROM 'done') AS open,
                   count(k.id) FILTER (WHERE bc.role = 'doing') AS doing,
                   count(k.id) FILTER (WHERE bc.role = 'review') AS review,
                   count(k.id) FILTER (WHERE k.blocked_reason IS NOT NULL AND bc.role IS DISTINCT FROM 'done')
                     AS blocked,
                   count(k.id) FILTER (WHERE k.due_date < current_date AND bc.role IS DISTINCT FROM 'done')
                     AS overdue
            FROM boards b JOIN collections c ON c.id = b.collection_id
            LEFT JOIN cards k ON k.board_id = b.id AND k.archived_at IS NULL
            LEFT JOIN board_columns bc ON bc.id = k.column_id
            WHERE ($1::text IS NULL OR c.name = $1) AND ($2::uuid[] IS NULL OR b.folder_id = ANY($2))
              AND ($3 OR b.archived_at IS NULL)
            GROUP BY b.id, c.name
            ORDER BY c.name, lower(b.name)
            """,
            collection,
            folder_ids,
            include_archived,
        )
    visible = [r for r in rows if p.can_access_collection(r["collection"])]
    paths: dict[str, dict[uuid.UUID, str]] = {}
    out = []
    for r in visible:
        if r["collection"] not in paths:
            paths[r["collection"]] = await _folder_paths(p, r["collection"])
        item = record(r)
        assert item is not None
        item["folder_path"] = paths[r["collection"]].get(r["folder_id"]) if r["folder_id"] else None
        item.pop("folder_id")
        if not r["archived_at"]:
            item.pop("archived_at")
        out.append(item)
    return out


async def _find_board_id(
    conn: asyncpg.Connection, p: Principal, collection: str, folder_path: str | None, name: str
) -> uuid.UUID:
    cid = await folders._collection(conn, p, collection)
    fid = None
    if folder_path:
        fid, _, _ = await folders._resolve_path(conn, p, cid, collection, folder_path, create=False)
    bid = await conn.fetchval(
        "SELECT id FROM boards WHERE collection_id = $1 AND folder_id IS NOT DISTINCT FROM $2 "
        "AND lower(name) = lower($3) AND archived_at IS NULL",
        cid,
        fid,
        " ".join(name.split()),
    )
    if bid is None:
        where = f"{collection}/{folder_path}" if folder_path else collection
        raise NotFound(f"Quadro '{name}' não existe em '{where}' (veja list_boards).")
    return bid


async def get_board(
    p: Principal,
    board_id: str | None = None,
    *,
    collection: str | None = None,
    folder_path: str | None = None,
    name: str | None = None,
    include_done: bool = False,
) -> dict:
    """O quadro com as colunas e os cartões (resumidos). Concluídos: só os últimos, salvo include_done."""
    p.require("read")
    async with pool().acquire() as conn:
        if board_id is None:
            if not (collection and name):
                raise KBError("Informe board_id, ou collection + name (e folder_path, se o quadro estiver numa pasta).")
            bid = await _find_board_id(conn, p, collection, folder_path, name)
        else:
            bid = parse_uuid(board_id, "board_id")
        board = await _load_board(conn, p, bid)
        async with conn.transaction():
            await _mark_stale(conn, board_ids=[bid])
        cols = await _columns(conn, bid)
        cards = await conn.fetch(
            _CARD_SELECT + " WHERE k.board_id = $1 AND k.archived_at IS NULL ORDER BY k.position, k.created_at", bid
        )
        path = await folders._folder_path(conn, board["folder_id"])
    now = _now()
    by_col: dict[uuid.UUID, list[dict]] = {c["id"]: [] for c in cols}
    for row in cards:
        by_col.setdefault(row["column_id"], []).append(_card_summary(row, now))
    columns_out = []
    for col in cols:
        items = by_col.get(col["id"], [])
        entry: dict[str, Any] = {
            "id": str(col["id"]),
            "name": col["name"],
            "role": col["role"],
            "wip_limit": col["wip_limit"],
            "count": len(items),
        }
        if col["role"] == "done" and not include_done and len(items) > DONE_CARDS_SHOWN:
            items = sorted(items, key=lambda c: c.get("done_at") or "", reverse=True)[:DONE_CARDS_SHOWN]
            entry["hidden"] = entry["count"] - len(items)
        entry["cards"] = items
        columns_out.append(entry)
    return {
        "id": str(board["id"]),
        "name": board["name"],
        "collection": board["collection"],
        "folder_path": path,
        "description": board["description"],
        "owner": board["owner"],
        "archived": board["archived_at"] is not None or None,
        "columns": columns_out,
    }


async def create_board(
    p: Principal,
    collection: str,
    name: str,
    folder_path: str | None = None,
    description: str | None = None,
    columns: list[Any] | None = None,
    owner: str | None = None,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Quadro novo na pasta (criada se faltar). Sem columns: Ideias · Pronto para pegar · Em andamento ·
    Aguardando revisão · Feito."""
    p.require("write")
    clean_name = _clean(name, "Nome do quadro", BOARD_NAME_MAX)
    spec = _columns_spec(columns)
    async with pool().acquire() as conn, conn.transaction():
        cid = await folders._collection(conn, p, collection)
        await folders._lock_collection(conn, cid)
        fid, path = None, None
        if folder_path and folder_path.strip():
            fid, path, _ = await folders._resolve_path(conn, p, cid, collection, folder_path, create=True)
        try:
            bid = await conn.fetchval(
                "INSERT INTO boards (collection_id, folder_id, name, description, owner, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                cid,
                fid,
                clean_name,
                (description or "").strip() or None,
                owner or p.actor,
                p.actor,
            )
        except asyncpg.UniqueViolationError as e:
            raise KBError(f"Já existe um quadro chamado '{clean_name}' nesta pasta.") from e
        for i, (col_name, role) in enumerate(spec):
            await conn.execute(
                "INSERT INTO board_columns (board_id, name, role, position) VALUES ($1, $2, $3, $4)",
                bid,
                col_name,
                role,
                i,
            )
        await _event(
            conn,
            p,
            origin,
            bid,
            None,
            "board.create",
            {"name": (None, clean_name), "folder_path": (None, path), "columns": (None, [c for c, _ in spec])},
        )
    return await get_board(p, str(bid))


async def update_board(
    p: Principal,
    board_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    owner: str | None = None,
    folder_path: str | None = None,
    archived: bool | None = None,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Renomeia, descreve, troca o dono, move para outra pasta da coleção ("" = raiz) ou arquiva."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        board = await _load_board(conn, p, board_id, lock=True)
        if board["archived_at"] is not None and archived is not False:
            raise KBError(f"O quadro '{board['name']}' está arquivado (archived=false restaura).")
        await folders._lock_collection(conn, board["collection_id"])
        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = _clean(name, "Nome do quadro", BOARD_NAME_MAX)
        if description is not None:
            updates["description"] = description.strip() or None
        if owner is not None:
            updates["owner"] = owner.strip() or None
        before_path = await folders._folder_path(conn, board["folder_id"])
        changes = _diff(board, updates)
        if folder_path is not None:
            fid = None
            if folder_path.strip():
                fid, _, _ = await folders._resolve_path(
                    conn, p, board["collection_id"], board["collection"], folder_path, create=True
                )
            if fid != board["folder_id"]:
                updates["folder_id"] = fid
                changes["folder_path"] = (before_path, await folders._folder_path(conn, fid))
        if archived is not None and archived != (board["archived_at"] is not None):
            updates["archived_at"] = _now() if archived else None
            changes["archived"] = (not archived, archived)
        changes.pop("folder_id", None)
        changes.pop("archived_at", None)
        if updates:
            sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(updates, start=2))
            try:
                await conn.execute(
                    f"UPDATE boards SET {sets}, updated_at = now() WHERE id = $1", board["id"], *updates.values()
                )
            except asyncpg.UniqueViolationError as e:
                raise KBError("Já existe um quadro ativo com esse nome nesse lugar.") from e
        if changes:
            kind = "board.archive" if archived and "archived" in changes else "board.update"
            await _event(conn, p, origin, board["id"], None, kind, changes)
    return await get_board(p, board_id)


# ---------------------------------------------------------------- colunas


async def _renumber(conn: asyncpg.Connection, board_id: uuid.UUID, ordered_ids: list[uuid.UUID]) -> None:
    for i, cid in enumerate(ordered_ids):
        await conn.execute("UPDATE board_columns SET position = $2 WHERE id = $1 AND board_id = $3", cid, i, board_id)


async def create_column(
    p: Principal,
    board_id: str,
    name: str,
    role: str | None = None,
    position: int | None = None,
    wip_limit: int | None = None,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Coluna nova (no fim, ou na posição 0-based)."""
    p.require("write")
    clean = _clean(name, "Nome da coluna", COLUMN_NAME_MAX)
    role_value = _role(role)
    async with pool().acquire() as conn, conn.transaction():
        board = await _load_board(conn, p, board_id, lock=True)
        _writable_board(board)
        cols = await _columns(conn, board["id"])
        if len(cols) >= 30:
            raise KBError("Um quadro tem no máximo 30 colunas.")
        if role_value and any(c["role"] == role_value for c in cols):
            other = next(c["name"] for c in cols if c["role"] == role_value)
            raise KBError(f"O papel '{role_value}' já é da coluna '{other}'.")
        new_id = await conn.fetchval(
            "INSERT INTO board_columns (board_id, name, role, position, wip_limit) VALUES ($1, $2, $3, $4, $5) "
            "RETURNING id",
            board["id"],
            clean,
            role_value,
            len(cols),
            wip_limit or None,
        )
        order = [c["id"] for c in cols]
        order.insert(len(order) if position is None else max(0, min(position, len(order))), new_id)
        await _renumber(conn, board["id"], order)
        await _event(
            conn, p, origin, board["id"], None, "column.create", {"name": (None, clean), "role": (None, role_value)}
        )
    return {"id": str(new_id), "board_id": str(board["id"]), "name": clean, "role": role_value}


async def _load_column(conn: asyncpg.Connection, p: Principal, column_id: str) -> tuple[dict, dict]:
    row = await conn.fetchrow(
        "SELECT id, board_id, name, role, position, wip_limit FROM board_columns WHERE id = $1",
        parse_uuid(column_id, "column_id"),
    )
    if row is None:
        raise NotFound(f"Coluna {column_id} não encontrada.")
    board = await _load_board(conn, p, row["board_id"], lock=True)
    _writable_board(board)
    return dict(row), board


async def update_column(
    p: Principal,
    column_id: str,
    *,
    name: str | None = None,
    role: str | None = None,
    position: int | None = None,
    wip_limit: int | None = None,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Renomeia, troca o papel (role="" tira), reordena (posição 0-based) ou muda o limite (0 tira)."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        col, board = await _load_column(conn, p, column_id)
        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = _clean(name, "Nome da coluna", COLUMN_NAME_MAX)
        if role is not None:
            updates["role"] = _role(role)
        if wip_limit is not None:
            updates["wip_limit"] = wip_limit or None
        changes = _diff(col, updates)
        if updates:
            sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(updates, start=2))
            try:
                await conn.execute(f"UPDATE board_columns SET {sets} WHERE id = $1", col["id"], *updates.values())
            except asyncpg.UniqueViolationError as e:
                raise KBError(f"O papel '{updates.get('role')}' já é de outra coluna deste quadro.") from e
        if position is not None:
            cols = await _columns(conn, board["id"])
            order = [c["id"] for c in cols if c["id"] != col["id"]]
            target = max(0, min(position, len(order)))
            old_index = [c["id"] for c in cols].index(col["id"])
            if target != old_index:
                order.insert(target, col["id"])
                await _renumber(conn, board["id"], order)
                changes["position"] = (old_index, target)
        if changes:
            await _event(conn, p, origin, board["id"], None, "column.update", changes, note=col["name"])
    return {"id": column_id, "board_id": str(board["id"]), **{k: v for k, (_, v) in changes.items()}}


async def delete_column(
    p: Principal, column_id: str, move_cards_to: str | None = None, origin: Origin = NO_ORIGIN
) -> dict:
    """Apaga a coluna. Se tiver cartões, informe move_cards_to (id, papel ou nome de outra coluna)."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        col, board = await _load_column(conn, p, column_id)
        cols = await _columns(conn, board["id"])
        if len(cols) == 1:
            raise KBError("O quadro precisa de pelo menos uma coluna.")
        count = await conn.fetchval("SELECT count(*) FROM cards WHERE column_id = $1", col["id"])
        moved_to = None
        if count:
            if not move_cards_to:
                raise KBError(f"A coluna '{col['name']}' tem {count} cartão(ões): informe move_cards_to.")
            target = await _resolve_column(conn, board["id"], move_cards_to)
            if target["id"] == col["id"]:
                raise KBError("move_cards_to precisa ser outra coluna.")
            await conn.execute(
                "UPDATE cards SET column_id = $2, updated_at = now() WHERE column_id = $1", col["id"], target["id"]
            )
            moved_to = target["name"]
        await conn.execute("DELETE FROM board_columns WHERE id = $1", col["id"])
        await _renumber(conn, board["id"], [c["id"] for c in cols if c["id"] != col["id"]])
        await _event(
            conn,
            p,
            origin,
            board["id"],
            None,
            "column.delete",
            {"name": (col["name"], None)},
            note=f"{count} cartão(ões) movidos para '{moved_to}'" if count else None,
        )
    return {"deleted": True, "id": column_id, "moved_cards": count, "moved_to": moved_to}


# ---------------------------------------------------------------- cartões


async def _assignee(conn: asyncpg.Connection, value: str | None) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """'agent:<slug>', o slug de um agente ou o usuário da dash → (agente, usuário). "" limpa."""
    if not value or not value.strip():
        return None, None
    ref = value.strip()
    slug = ref.removeprefix("agent:")
    agent_id = await conn.fetchval("SELECT id FROM agents WHERE slug = $1 AND status = 'active'", slug)
    if agent_id is not None:
        return agent_id, None
    user_id = await conn.fetchval(
        "SELECT id FROM dash_users WHERE username = $1 AND disabled_at IS NULL", ref.removeprefix("dash:")
    )
    if user_id is not None:
        return None, user_id
    raise NotFound(f"Responsável '{value}' não é agente ativo nem usuário da dash.")


async def _insert_items(conn: asyncpg.Connection, card_id: uuid.UUID, items: list[str]) -> list[str]:
    start = await conn.fetchval("SELECT coalesce(max(position), -1) + 1 FROM card_items WHERE card_id = $1", card_id)
    added = []
    for i, text in enumerate(items):
        clean = _clean(text, "Item do checklist", ITEM_TEXT_MAX, required=False)
        if not clean:
            continue
        await conn.execute(
            "INSERT INTO card_items (card_id, position, text) VALUES ($1, $2, $3)", card_id, start + i, clean
        )
        added.append(clean)
    if len(added) + start > 100:
        raise KBError("Um cartão tem no máximo 100 itens no checklist.")
    return added


async def create_card(
    p: Principal,
    board_id: str,
    title: str,
    description: str | None = None,
    checklist: list[str] | None = None,
    labels: list[str] | None = None,
    due: date | str | None = None,
    column: str | None = None,
    assignee: str | None = None,
    repo: str | None = None,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Cartão novo. Sem column: vai para a coluna de papel ready (ou backlog, ou a primeira)."""
    p.require("write")
    clean_title = _clean(title, "Título do cartão", CARD_TITLE_MAX)
    due_value = _parse_due(due)
    label_values = _labels(labels)
    async with pool().acquire() as conn, conn.transaction():
        board = await _load_board(conn, p, board_id, lock=True)
        _writable_board(board)
        if column:
            col = await _resolve_column(conn, board["id"], column)
        else:
            col = await _column_by_role(conn, board["id"], "ready", "backlog") or (await _columns(conn, board["id"]))[0]
        agent_id, user_id = await _assignee(conn, assignee)
        card_id = await conn.fetchval(
            """
            INSERT INTO cards (board_id, column_id, title, description, position, due_date, labels,
                               assignee_agent_id, assignee_user_id, repo, created_by, updated_by,
                               done_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11, CASE WHEN $12 THEN now() END)
            RETURNING id
            """,
            board["id"],
            col["id"],
            clean_title,
            (description or "").strip() or None,
            await _next_position(conn, col["id"]),
            due_value,
            label_values,
            agent_id,
            user_id,
            (repo or "").strip() or None,
            p.actor,
            col["role"] == "done",
        )
        items = await _insert_items(conn, card_id, checklist or [])
        await _event(
            conn,
            p,
            origin,
            board["id"],
            card_id,
            "card.create",
            {"title": (None, clean_title), "column": (None, col["name"]), "checklist": (None, len(items) or None)},
        )
    return await get_card(p, str(card_id))


async def get_card(p: Principal, card_id: str, events: int = 15) -> dict:
    """Cartão completo: campos, checklist, notas ligadas e os últimos eventos."""
    p.require("read")
    async with pool().acquire() as conn:
        card = await _load_card(conn, p, card_id)
        async with conn.transaction():
            await _mark_stale(conn, board_ids=[card["board_id"]])
        row = await conn.fetchrow(_CARD_SELECT + " WHERE k.id = $1", card["id"])
        items = await conn.fetch(
            "SELECT id, text, done, done_by, done_at FROM card_items WHERE card_id = $1 ORDER BY position, id",
            card["id"],
        )
        docs = await conn.fetch(
            "SELECT d.id, d.title FROM card_documents cd JOIN documents d ON d.id = cd.document_id "
            "WHERE cd.card_id = $1 AND d.status = 'active' ORDER BY cd.created_at",
            card["id"],
        )
        recent = await conn.fetch(
            "SELECT * FROM board_events WHERE card_id = $1 ORDER BY id DESC LIMIT $2", card["id"], max(0, events)
        )
    out = _card_summary(row)
    out.update(
        {
            "board_id": str(card["board_id"]),
            "board": card["board_name"],
            "collection": card["collection"],
            "description": card["description"],
            "items": [
                {"id": i["id"], "text": i["text"], "done": i["done"]}
                | (
                    {"done_by": i["done_by"], "done_at": i["done_at"].isoformat(timespec="minutes")}
                    if i["done"]
                    else {}
                )
                for i in items
            ],
            "documents": records(docs) or None,
            "created_by": card["created_by"],
            "created_at": card["created_at"].isoformat(timespec="minutes"),
            "log": [_event_out(e, drop=("board_id", "card_id")) for e in recent] or None,
        }
    )
    return out


async def _move_to(
    conn: asyncpg.Connection, card: dict, col: dict, extra: dict[str, Any] | None = None
) -> dict[str, tuple[Any, Any]]:
    """Muda a coluna (fim da coluna) e acerta done_at; devolve o antes → depois."""
    changes: dict[str, tuple[Any, Any]] = {}
    sets = dict(extra or {})
    if col["id"] != card["column_id"]:
        sets["column_id"] = col["id"]
        sets["position"] = await _next_position(conn, col["id"])
        changes["column"] = (card["column_name"], col["name"])
        if col["role"] == "done" and card["done_at"] is None:
            sets["done_at"] = _now()
        elif col["role"] != "done" and card["done_at"] is not None:
            sets["done_at"] = None
    if sets:
        keys = list(sets)
        assignments = ", ".join(f"{k} = ${i}" for i, k in enumerate(keys, start=2))
        await conn.execute(
            f"UPDATE cards SET {assignments}, updated_at = now() WHERE id = $1", card["id"], *sets.values()
        )
    return changes


async def update_card(
    p: Principal,
    card_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    due: date | str | None = None,
    labels: list[str] | None = None,
    repo: str | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    mr_url: str | None = None,
    ci_status: str | None = None,
    column: str | None = None,
    position: int | None = None,
    assignee: str | None = None,
    archived: bool | None = None,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Edita campos (texto "" limpa), move de coluna (id, papel ou nome), reordena (posição 0-based na
    coluna) ou arquiva. Renova a posse de quem segura."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        if card["board_archived_at"] is not None:
            raise KBError(f"O quadro '{card['board_name']}' está arquivado.")
        if card["archived_at"] is not None and archived is not False:
            raise KBError("O cartão está arquivado (archived=false restaura).")
        _guard(p, card)
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = _clean(title, "Título do cartão", CARD_TITLE_MAX)
        values = {
            "description": description,
            "repo": repo,
            "worktree": worktree,
            "branch": branch,
            "mr_url": mr_url,
            "ci_status": ci_status,
        }
        for key in _TEXT_FIELDS:
            if values[key] is not None:
                text = values[key].strip()  # type: ignore[union-attr]
                updates[key] = (text if key == "description" else " ".join(text.split())[:500]) or None
        if due is not None:
            updates["due_date"] = _parse_due(due)
        if labels is not None:
            updates["labels"] = _labels(labels)
        if assignee is not None:
            updates["assignee_agent_id"], updates["assignee_user_id"] = await _assignee(conn, assignee)
        changes = _diff(card, updates)
        for key in ("assignee_agent_id", "assignee_user_id"):
            if key in changes:
                changes.pop(key)
                changes["assignee"] = (None, assignee or None)
        if updates:
            updates["updated_by"] = p.actor
            sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(updates, start=2))
            await conn.execute(
                f"UPDATE cards SET {sets}, updated_at = now() WHERE id = $1", card["id"], *updates.values()
            )
        kind = "card.update"
        if column is not None:
            col = await _resolve_column(conn, card["board_id"], column)
            moved = await _move_to(conn, card, col)
            if moved:
                changes.update(moved)
                kind = "card.move" if len(changes) == 1 else kind
                card["column_id"] = col["id"]
        if position is not None:
            siblings = [
                r["id"]
                for r in await conn.fetch(
                    "SELECT id FROM cards WHERE column_id = $1 AND archived_at IS NULL AND id <> $2 "
                    "ORDER BY position, created_at",
                    card["column_id"],
                    card["id"],
                )
            ]
            siblings.insert(max(0, min(position, len(siblings))), card["id"])
            for i, sid in enumerate(siblings):
                await conn.execute("UPDATE cards SET position = $2 WHERE id = $1", sid, float(i + 1))
            changes.setdefault("position", (None, position))
        if archived is not None and archived != (card["archived_at"] is not None):
            await conn.execute(
                "UPDATE cards SET archived_at = $2, updated_at = now() WHERE id = $1",
                card["id"],
                _now() if archived else None,
            )
            changes["archived"] = (not archived, archived)
            kind = "card.archive" if archived else "card.restore"
        if changes:
            await _event(conn, p, origin, card["board_id"], card["id"], kind, changes)
            await _renew(conn, p, card["id"])
    return await get_card(p, card_id)


async def start_card(
    p: Principal, card_id: str, repo: str | None = None, force: bool = False, origin: Origin = NO_ORIGIN
) -> dict:
    """Pega o cartão para esta sessão (posse de 2 h, renovada a cada atualização), move para a coluna de
    papel doing e sugere worktree e branch. force=true retoma uma posse sua de outra sessão (ou, para
    pessoas e admin, a de qualquer um)."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        _writable_card(card)
        if card["role"] == "done":
            raise KBError("O cartão já está concluído; mova-o para outra coluna antes de pegar.")
        state = claim_state(card)
        holder, holder_session = card["claimed_by"], card["claim_session"]
        kind = "claim.start"
        if state == "active":
            if holder != p.actor:
                if not (force and _is_person(p)):
                    raise PermissionDenied(
                        f"O cartão já está com {holder} (sessão {holder_session or '?'}, worktree "
                        f"{card['worktree'] or '?'}) até {card['lease_until'].isoformat(timespec='minutes')}."
                    )
                kind = "claim.takeover"
            elif holder_session and origin.session and holder_session != origin.session:
                if not force:
                    raise KBError(
                        f"O cartão está com outra sessão sua ({holder_session}, worktree {card['worktree'] or '?'}). "
                        "Se é você retomando o trabalho, chame start_card de novo com force=true."
                    )
                kind = "claim.takeover"
            else:
                kind = "claim.renew"
        elif state == "stale":
            kind = "claim.takeover" if (holder, holder_session) != (p.actor, origin.session) else "claim.resume"
        extra: dict[str, Any] = {
            "claimed_by": p.actor,
            "claim_session": origin.session,
            "lease_until": _now() + _lease(),
            "stale_logged_at": None,
            "updated_by": p.actor,
        }
        if kind in ("claim.start", "claim.takeover"):
            extra["claimed_at"] = _now()
        if repo and repo.strip():
            extra["repo"] = " ".join(repo.split())
        doing = await _column_by_role(conn, card["board_id"], "doing")
        changes = await _move_to(conn, card, doing or {"id": card["column_id"], "role": card["role"]}, extra)
        if holder != p.actor or holder_session != origin.session:
            changes["claimed_by"] = (f"{holder} ({holder_session})" if holder_session else holder, p.actor)
        await _event(conn, p, origin, card["board_id"], card["id"], kind, changes or None)
    out = await get_card(p, card_id, events=5)
    slug = slugify(out["title"])
    repo_name = out.get("repo") or "<repo>"
    out["suggested"] = {"worktree": f"worktrees/{repo_name}-{slug}", "branch": f"feat/{slug}"}
    out["hint"] = (
        "Grave o worktree e a branch reais com update_card; marque os passos com check_item; registre commit, "
        "MR e CI com log_card; no fim, submit_card (MR aberta) ou finish_card. Parou no meio: release_card."
    )
    return out


async def _find_item(conn: asyncpg.Connection, card_id: uuid.UUID, item_id: int | None, text: str | None) -> dict:
    if item_id is not None:
        row = await conn.fetchrow("SELECT * FROM card_items WHERE id = $1 AND card_id = $2", item_id, card_id)
        if row is None:
            raise NotFound(f"Item {item_id} não é deste cartão.")
        return dict(row)
    if not text or not text.strip():
        raise KBError("Informe item_id ou o texto do item.")
    rows = [dict(r) for r in await conn.fetch("SELECT * FROM card_items WHERE card_id = $1 ORDER BY position", card_id)]
    wanted = " ".join(text.split()).lower()
    exact = [r for r in rows if r["text"].lower() == wanted]
    if exact:
        return exact[0]
    partial = [r for r in rows if wanted in r["text"].lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise NotFound(f"Nenhum item do checklist tem '{text}'.")
    raise KBError(f"'{text}' bate com {len(partial)} itens; use item_id (veja get_card).")


async def check_item(
    p: Principal,
    card_id: str,
    item_id: int | None = None,
    text: str | None = None,
    done: bool = True,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Marca (ou desmarca, done=false) um passo do checklist, por id ou pelo texto."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        _writable_card(card)
        _guard(p, card)
        item = await _find_item(conn, card["id"], item_id, text)
        if item["done"] != done:
            await conn.execute(
                "UPDATE card_items SET done = $2, done_at = CASE WHEN $2 THEN now() END, "
                "done_by = CASE WHEN $2 THEN $3 END WHERE id = $1",
                item["id"],
                done,
                p.actor,
            )
            await conn.execute(
                "UPDATE cards SET updated_at = now(), updated_by = $2 WHERE id = $1", card["id"], p.actor
            )
            await _event(
                conn,
                p,
                origin,
                card["board_id"],
                card["id"],
                "item.check" if done else "item.uncheck",
                {"done": (item["done"], done)},
                note=item["text"],
            )
        await _renew(conn, p, card["id"])
        total, finished = await conn.fetchrow(
            "SELECT count(*), count(*) FILTER (WHERE done) FROM card_items WHERE card_id = $1", card["id"]
        )
        next_item = await conn.fetchval(
            "SELECT text FROM card_items WHERE card_id = $1 AND NOT done ORDER BY position, id LIMIT 1", card["id"]
        )
    return {
        "card_id": card_id,
        "item_id": item["id"],
        "done": done,
        "progress": f"{finished}/{total}",
        "next_item": next_item,
    }


async def add_items(p: Principal, card_id: str, items: list[str], origin: Origin = NO_ORIGIN) -> dict:
    """Acrescenta passos no fim do checklist."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        _writable_card(card)
        _guard(p, card)
        added = await _insert_items(conn, card["id"], items)
        if not added:
            raise KBError("Nenhum item para acrescentar.")
        await _touch(conn, card["id"], p.actor)
        await _event(conn, p, origin, card["board_id"], card["id"], "item.add", {"items": (None, added)})
        await _renew(conn, p, card["id"])
    return await get_card(p, card_id, events=0)


async def edit_item(
    p: Principal,
    card_id: str,
    item_id: int,
    text: str | None = None,
    delete: bool = False,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Reescreve (text) ou apaga (delete=true) um passo do checklist."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        _writable_card(card)
        _guard(p, card)
        item = await _find_item(conn, card["id"], item_id, None)
        if delete:
            await conn.execute("DELETE FROM card_items WHERE id = $1", item["id"])
            await _event(conn, p, origin, card["board_id"], card["id"], "item.delete", {"item": (item["text"], None)})
        else:
            new = _clean(text, "Item do checklist", ITEM_TEXT_MAX)
            if new != item["text"]:
                await conn.execute("UPDATE card_items SET text = $2 WHERE id = $1", item["id"], new)
                await _event(conn, p, origin, card["board_id"], card["id"], "item.edit", {"item": (item["text"], new)})
        await _touch(conn, card["id"], p.actor)
        await _renew(conn, p, card["id"])
    return await get_card(p, card_id, events=0)


async def log_card(p: Principal, card_id: str, text: str, kind: str = "note", origin: Origin = NO_ORIGIN) -> dict:
    """Registra um fato no log do cartão. kind: note|commit|mr|ci|decision|blocker."""
    p.require("write")
    kind = (kind or "note").strip().lower()
    if kind not in LOG_KINDS:
        raise KBError(f"kind inválido: '{kind}'. Use um de {', '.join(LOG_KINDS)}.")
    note = (text or "").strip()
    if not note:
        raise KBError("O texto do log não pode ficar vazio.")
    if len(note) > 4000:
        raise KBError("O texto do log passa de 4000 caracteres.")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        await _event(conn, p, origin, card["board_id"], card["id"], f"log.{kind}", note=note)
        await _renew(conn, p, card["id"])
    return {"card_id": card_id, "logged": True, "kind": kind}


async def block_card(p: Principal, card_id: str, reason: str | None, origin: Origin = NO_ORIGIN) -> dict:
    """Marca o cartão como bloqueado com o motivo (fica vermelho na dash). reason vazio desbloqueia."""
    p.require("write")
    reason_clean = (reason or "").strip()[:1000] or None
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        _writable_card(card)
        _guard(p, card)
        if card["blocked_reason"] != reason_clean:
            await conn.execute(
                "UPDATE cards SET blocked_reason = $2, updated_at = now(), updated_by = $3 WHERE id = $1",
                card["id"],
                reason_clean,
                p.actor,
            )
            await _event(
                conn,
                p,
                origin,
                card["board_id"],
                card["id"],
                "card.block" if reason_clean else "card.unblock",
                {"blocked": (card["blocked_reason"], reason_clean)},
            )
        await _renew(conn, p, card["id"])
    return {"card_id": card_id, "blocked": reason_clean}


async def release_card(
    p: Principal, card_id: str, next_step: str, force: bool = False, origin: Origin = NO_ORIGIN
) -> dict:
    """Solta o cartão parado no meio, com o próximo passo escrito; volta para a coluna de papel ready.
    force=true (pessoa ou admin) libera a posse de outra sessão."""
    p.require("write")
    step = (next_step or "").strip()
    if not step:
        raise KBError("Escreva o próximo passo (quem pegar depois continua dele).")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        _writable_card(card)
        state = claim_state(card)
        if state is None:
            raise KBError("O cartão não está com ninguém.")
        forced = state == "active" and card["claimed_by"] != p.actor
        if forced and not (force and _is_person(p)):
            raise PermissionDenied(f"O cartão está com {card['claimed_by']}; só uma pessoa ou admin força a liberação.")
        extra = {
            "claimed_by": None,
            "claim_session": None,
            "claimed_at": None,
            "lease_until": None,
            "stale_logged_at": None,
            "updated_by": p.actor,
        }
        ready = await _column_by_role(conn, card["board_id"], "ready", "backlog")
        target = ready if ready and card["role"] == "doing" else {"id": card["column_id"], "role": card["role"]}
        changes = await _move_to(conn, card, target, extra)
        changes["claimed_by"] = (card["claimed_by"], None)
        await _event(
            conn,
            p,
            origin,
            card["board_id"],
            card["id"],
            "claim.force_release" if forced else "claim.release",
            changes,
            note=step,
        )
    return {"card_id": card_id, "released": True, "column": target.get("name", card["column_name"])}


async def submit_card(
    p: Principal, card_id: str, summary: str, mr_url: str | None = None, origin: Origin = NO_ORIGIN
) -> dict:
    """Manda para revisão humana (coluna de papel review), com o resumo e o link da MR. Encerra a posse."""
    return await _close(p, card_id, summary, "review", "card.submit", origin, mr_url=mr_url)


async def finish_card(
    p: Principal,
    card_id: str,
    summary: str,
    append_to_document: str | None = None,
    origin: Origin = NO_ORIGIN,
) -> dict:
    """Conclui (coluna de papel done) com o resumo. append_to_document: id de uma nota (o plano) que
    recebe o resumo no fim."""
    result = await _close(p, card_id, summary, "done", "card.finish", origin)
    if append_to_document:
        stamp = _now().date().isoformat()
        await documents.edit_document(
            p,
            append_to_document,
            f"cartão concluído: {result['title']}",
            append=f"\n\n**{stamp} — cartão concluído: {result['title']}.** {summary.strip()}",
        )
        result["appended_to"] = append_to_document
    return result


async def _close(
    p: Principal, card_id: str, summary: str, role: str, kind: str, origin: Origin, mr_url: str | None = None
) -> dict:
    p.require("write")
    text = (summary or "").strip()
    if not text:
        raise KBError("Escreva o resumo do que foi feito.")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        _writable_card(card)
        _guard(p, card)
        col = await _column_by_role(conn, card["board_id"], role)
        if col is None:
            raise KBError(f"Este quadro não tem coluna com o papel '{role}' (update_column define o papel).")
        extra: dict[str, Any] = {"lease_until": None, "stale_logged_at": None, "updated_by": p.actor}
        if card["claimed_by"] is None:
            extra["claimed_by"] = p.actor  # quem fez fica registrado mesmo sem ter pegado
        if role == "done":
            extra["blocked_reason"] = None
        if mr_url and mr_url.strip():
            extra["mr_url"] = mr_url.strip()
        changes = await _move_to(conn, card, col, extra)
        if mr_url and mr_url.strip() != card["mr_url"]:
            changes["mr_url"] = (card["mr_url"], mr_url.strip())
        await _event(conn, p, origin, card["board_id"], card["id"], kind, changes or None, note=text)
    return {"card_id": card_id, "title": card["title"], "column": col["name"], "role": role}


async def link_card_document(
    p: Principal, card_id: str, document_id: str, unlink: bool = False, origin: Origin = NO_ORIGIN
) -> dict:
    """Liga (ou desliga) uma nota ao cartão: o plano, a ata, o contexto."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        card = await _load_card(conn, p, card_id, lock=True)
        doc = await conn.fetchrow(
            "SELECT d.id, d.title, c.name AS collection FROM documents d JOIN collections c ON c.id = d.collection_id "
            "WHERE d.id = $1 AND d.status = 'active'",
            parse_uuid(document_id, "document_id"),
        )
        if doc is None:
            raise NotFound(f"Documento {document_id} não encontrado.")
        p.require_collection(doc["collection"])
        if unlink:
            status = await conn.execute(
                "DELETE FROM card_documents WHERE card_id = $1 AND document_id = $2", card["id"], doc["id"]
            )
            changed = status.endswith(" 1")
        else:
            status = await conn.execute(
                "INSERT INTO card_documents (card_id, document_id, created_by) VALUES ($1, $2, $3) "
                "ON CONFLICT DO NOTHING",
                card["id"],
                doc["id"],
                p.actor,
            )
            changed = status.endswith(" 1")
        if changed:
            await _event(
                conn,
                p,
                origin,
                card["board_id"],
                card["id"],
                "card.unlink" if unlink else "card.link",
                {"document": (doc["title"], None) if unlink else (None, doc["title"])},
                note=str(doc["id"]),
            )
    return {"card_id": card_id, "document_id": document_id, "linked": not unlink, "changed": changed}


async def cards_for_document(p: Principal, document_id: str) -> list[dict]:
    """Cartões ligados a uma nota (para a nota mostrar)."""
    p.require("read")
    rows = await pool().fetch(
        _CARD_SELECT.replace("SELECT k.id,", "SELECT c.name AS collection, b.name AS board_name, k.id,")
        + " JOIN boards b ON b.id = k.board_id JOIN collections c ON c.id = b.collection_id"
        " JOIN card_documents cd ON cd.card_id = k.id WHERE cd.document_id = $1 AND k.archived_at IS NULL",
        parse_uuid(document_id, "document_id"),
    )
    return [
        _card_summary(r) | {"board": r["board_name"], "collection": r["collection"]}
        for r in rows
        if p.can_access_collection(r["collection"])
    ]


async def search_cards(
    p: Principal, query: str, collection: str | None = None, include_done: bool = False, limit: int = 20
) -> list[dict]:
    """Busca por palavra no título e na descrição dos cartões (sem vetor)."""
    p.require("read")
    q = (query or "").strip()
    if not q:
        raise KBError("Informe o que buscar.")
    allowed = p.collection_filter([collection] if collection else None)
    rows = await pool().fetch(
        _CARD_SELECT.replace("SELECT k.id,", "SELECT c.name AS collection, b.name AS board_name, k.id,")
        + """
        JOIN boards b ON b.id = k.board_id JOIN collections c ON c.id = b.collection_id
        WHERE k.archived_at IS NULL AND b.archived_at IS NULL
          AND (k.tsv @@ websearch_to_tsquery('portuguese', $1) OR k.title ILIKE '%' || $1 || '%')
          AND ($2::text[] IS NULL OR c.name = ANY($2))
          AND ($3 OR bc.role IS DISTINCT FROM 'done')
        ORDER BY ts_rank(k.tsv, websearch_to_tsquery('portuguese', $1)) DESC, k.updated_at DESC
        LIMIT $4
        """,
        q,
        allowed,
        include_done,
        max(1, min(limit, 100)),
    )
    return [_card_summary(r) | {"board": r["board_name"], "collection": r["collection"]} for r in rows]


async def my_work(p: Principal, session: str | None = None, repo: str | None = None, actor: str | None = None) -> dict:
    """Cartões que esta conexão segura (ou deixou parados), os que esperam revisão e os atribuídos a ela.
    Usado no início da sessão para continuar de onde parou."""
    p.require("read")
    who = actor or p.actor
    if who != p.actor and not p.is_manager:
        raise PermissionDenied("Ver o trabalho de outro agente exige o escopo agents:manage.")
    agent_slug = who.removeprefix("agent:") if who.startswith("agent:") else None
    async with pool().acquire() as conn:
        async with conn.transaction():
            await _mark_stale(conn, actor=who)
        rows = await conn.fetch(
            _CARD_SELECT.replace("SELECT k.id,", "SELECT c.name AS collection, b.name AS board_name, k.id,")
            + """
            JOIN boards b ON b.id = k.board_id JOIN collections c ON c.id = b.collection_id
            WHERE k.archived_at IS NULL AND b.archived_at IS NULL AND bc.role IS DISTINCT FROM 'done'
              AND (k.claimed_by = $1 OR (a.slug IS NOT NULL AND a.slug = $2))
              AND ($3::text IS NULL OR k.repo = $3)
            ORDER BY k.updated_at DESC
            LIMIT 50
            """,
            who,
            agent_slug,
            (repo or "").strip() or None,
        )
        notes: dict[uuid.UUID, str] = {}
        if rows:
            for r in await conn.fetch(
                "SELECT DISTINCT ON (card_id) card_id, note FROM board_events "
                "WHERE card_id = ANY($1) AND kind IN ('claim.release', 'claim.force_release') AND note IS NOT NULL "
                "ORDER BY card_id, id DESC",
                [r["id"] for r in rows],
            ):
                notes[r["card_id"]] = r["note"]
    now = _now()
    groups: dict[str, list[dict]] = {"holding": [], "stale": [], "review": [], "assigned": []}
    for r in rows:
        if not p.can_access_collection(r["collection"]):
            continue
        card = _card_summary(r, now) | {"board": r["board_name"], "collection": r["collection"]}
        if r["id"] in notes:
            card["next_step"] = notes[r["id"]]
        if session and r["claim_session"] == session and card["claim"]:
            card["this_session"] = True
        state = card["claim"]
        if r["claimed_by"] == who and state == "active":
            groups["holding"].append(card)
        elif r["claimed_by"] == who and state == "stale":
            groups["stale"].append(card)
        elif r["claimed_by"] == who and r["role"] == "review":
            groups["review"].append(card)
        elif r["claimed_by"] is None or state is None:
            groups["assigned"].append(card)
    return {"actor": who, "session": session, **groups}


def my_work_lines(work: dict, limit: int = 10) -> list[str]:
    """Texto curto do my_work para o resumo do projeto (plugin do Claude Code)."""
    labels = {
        "holding": "segurando",
        "stale": "parado (posse vencida)",
        "review": "aguardando revisão",
        "assigned": "atribuído",
    }
    lines: list[str] = []
    for group, label in labels.items():
        for card in work[group]:
            if len(lines) >= limit:
                return lines
            bits = [f"- [{label}] {card['title']} ({card['board']}, id {card['id']})"]
            if card.get("progress"):
                bits.append(f"checklist {card['progress']}")
            if card.get("worktree"):
                bits.append(f"worktree {card['worktree']}")
            if card.get("next_step"):
                bits.append(f"próximo passo: {card['next_step']}")
            elif card.get("next_item"):
                bits.append(f"próximo item: {card['next_item']}")
            if card.get("this_session"):
                bits.append("desta sessão")
            lines.append(" · ".join(bits))
    return lines


async def active_cards(p: Principal, collection: str, limit: int = 10) -> list[dict]:
    """Cartões com posse valendo na coleção (quem está fazendo o quê agora)."""
    p.require("read")
    p.require_collection(collection)
    rows = await pool().fetch(
        _CARD_SELECT.replace("SELECT k.id,", "SELECT b.name AS board_name, k.id,")
        + """
        JOIN boards b ON b.id = k.board_id JOIN collections c ON c.id = b.collection_id
        WHERE c.name = $1 AND k.archived_at IS NULL AND b.archived_at IS NULL AND k.lease_until > now()
        ORDER BY k.updated_at DESC LIMIT $2
        """,
        collection,
        limit,
    )
    return [_card_summary(r) | {"board": r["board_name"]} for r in rows]


# ---------------------------------------------------------------- logs (somente leitura)


async def board_log(
    p: Principal,
    board_id: str,
    card_id: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    since: datetime | str | None = None,
    until: datetime | str | None = None,
    limit: int = 100,
    before_id: int | None = None,
) -> dict:
    """Linha do tempo de auditoria do quadro (mais recentes primeiro). kind filtra pelo começo
    ("card", "claim", "item.check"…); before_id pagina."""
    p.require("read")

    def when(value: datetime | str | None) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as e:
            raise KBError(f"Data inválida: '{value}'. Use AAAA-MM-DD ou ISO 8601.") from e
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    start, end = when(since), when(until)
    if isinstance(until, str) and len(until.strip()) == 10 and end is not None:
        end += timedelta(days=1)  # "até 2026-11-12" inclui o dia todo
    size = max(1, min(limit, EVENTS_MAX))
    async with pool().acquire() as conn:
        board = await _load_board(conn, p, board_id)
        rows = await conn.fetch(
            """
            SELECT * FROM board_events
            WHERE board_id = $1 AND ($2::uuid IS NULL OR card_id = $2)
              AND ($3::text IS NULL OR actor ILIKE '%' || $3 || '%')
              AND ($4::text IS NULL OR kind = $4 OR kind LIKE $4 || '.%')
              AND ($5::timestamptz IS NULL OR created_at >= $5) AND ($6::timestamptz IS NULL OR created_at < $6)
              AND ($7::bigint IS NULL OR id < $7)
            ORDER BY id DESC LIMIT $8
            """,
            board["id"],
            parse_uuid(card_id, "card_id") if card_id else None,
            actor or None,
            (kind or "").strip() or None,
            start,
            end,
            before_id,
            size + 1,
        )
    drop = ("board_id", "card_id") if card_id else ("board_id",)
    events = [_event_out(r, drop=drop) for r in rows[:size]]
    return {
        "board_id": str(board["id"]),
        "events": events,
        "next_before_id": events[-1]["id"] if len(rows) > size else None,
    }


async def card_log(p: Principal, card_id: str, limit: int = 100) -> dict:
    """Linha do tempo de auditoria de um cartão (mais recentes primeiro)."""
    p.require("read")
    async with pool().acquire() as conn:
        card = await _load_card(conn, p, card_id)
    return await board_log(p, str(card["board_id"]), card_id=card_id, limit=limit)


# ---------------------------------------------------------------- pastas


async def lift_boards_from_folder(
    conn: asyncpg.Connection, p: Principal, folder: dict, origin: Origin = NO_ORIGIN
) -> int:
    """Antes de excluir a pasta: os quadros sobem para a pasta-mãe; nome repetido lá ganha o nome da pasta."""
    rows = await conn.fetch(
        "SELECT id, name, archived_at FROM boards WHERE folder_id = $1 ORDER BY created_at", folder["id"]
    )
    for r in rows:
        name = r["name"]
        if r["archived_at"] is None and await conn.fetchval(
            "SELECT 1 FROM boards WHERE collection_id = $1 AND folder_id IS NOT DISTINCT FROM $2 "
            "AND lower(name) = lower($3) AND archived_at IS NULL",
            folder["collection_id"],
            folder["parent_id"],
            name,
        ):
            name = f"{name} ({folder['name']})"[:BOARD_NAME_MAX]
        await conn.execute(
            "UPDATE boards SET folder_id = $2, name = $3, updated_at = now() WHERE id = $1",
            r["id"],
            folder["parent_id"],
            name,
        )
        changes = {"folder": (folder["name"], None)}
        if name != r["name"]:
            changes["name"] = (r["name"], name)
        await _event(conn, p, origin, r["id"], None, "board.update", changes, note="pasta excluída: o quadro subiu")
    return len(rows)
