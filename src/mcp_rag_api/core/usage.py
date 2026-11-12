"""Gasto real de tokens das sessões (plan-web-38).

O plugin do Claude Code soma, do transcript da sessão, o que a API cobrou (entrada, saída, gravação e leitura
de cache) e manda o acumulado. Aqui fica a linha por sessão e as contas do Painel e do cartão.

Custo comparável entre sessões: "tokens equivalentes de entrada" (proporção de preço dos modelos Claude):
entrada + 1,25 × gravação de cache + 0,1 × leitura de cache + 5 × saída.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..db import pool
from ..security import KBError, NotFound, PermissionDenied, Principal
from .documents import parse_uuid

W_INPUT, W_CACHE_WRITE, W_CACHE_READ, W_OUTPUT = 1.0, 1.25, 0.1, 5.0
COST_SQL = (
    f"(input_tokens * {W_INPUT} + cache_write_tokens * {W_CACHE_WRITE} "
    f"+ cache_read_tokens * {W_CACHE_READ} + output_tokens * {W_OUTPUT})"
)
COUNTERS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "concilium_calls",
    "concilium_result_tokens",
)
MAX_COUNTER = 10**12


def cost(row: dict[str, Any]) -> float:
    return (
        row.get("input_tokens", 0) * W_INPUT
        + row.get("cache_write_tokens", 0) * W_CACHE_WRITE
        + row.get("cache_read_tokens", 0) * W_CACHE_READ
        + row.get("output_tokens", 0) * W_OUTPUT
    )


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def record_session(p: Principal, data: dict[str, Any]) -> dict:
    """Grava (ou atualiza) o acumulado de uma sessão. Basta o escopo read: é a telemetria do próprio
    cliente, sem conteúdo. Os contadores nunca diminuem (envio atrasado não apaga o mais novo)."""
    p.require("read")
    sid = str(data.get("session_id") or "").strip()
    if not 8 <= len(sid) <= 120:
        raise KBError("session_id inválido.")
    counters = {}
    for k in COUNTERS:
        v = data.get(k, 0)
        if not isinstance(v, int) or v < 0 or v > MAX_COUNTER:
            raise KBError(f"{k} precisa ser um inteiro entre 0 e {MAX_COUNTER}.")
        counters[k] = v
    collection = (data.get("collection") or "").strip() or None
    async with pool().acquire() as conn:
        cid = None
        if collection:
            p.require_collection(collection)
            cid = await conn.fetchval("SELECT id FROM collections WHERE name = $1", collection)
            if cid is None:
                raise NotFound(f"Coleção '{collection}' não encontrada.")
        aid = p.agent_id
        slug = (data.get("agent") or "").strip() or None
        if slug and slug != p.agent_slug:
            if not p.is_manager:
                raise PermissionDenied("Só o próprio agente (ou quem gerencia agentes) reporta o uso dele.")
            aid = await conn.fetchval("SELECT id FROM agents WHERE slug = $1", slug)
        owner = await conn.fetchval("SELECT reporter FROM session_usage WHERE session_id = $1", sid)
        if owner is not None and owner != p.actor and not p.has("admin"):
            raise PermissionDenied("Esta sessão foi reportada por outra credencial.")
        text = lambda k, n=120: str(data.get(k) or "").strip()[:n] or None  # noqa: E731
        await conn.execute(
            f"""
            INSERT INTO session_usage (session_id, collection_id, agent_id, reporter, client, model, repo,
              started_at, ended_at, {", ".join(COUNTERS)})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, {", ".join(f"${i}" for i in range(10, 10 + len(COUNTERS)))})
            ON CONFLICT (session_id) DO UPDATE SET
              collection_id = COALESCE(EXCLUDED.collection_id, session_usage.collection_id),
              agent_id = COALESCE(EXCLUDED.agent_id, session_usage.agent_id),
              client = COALESCE(EXCLUDED.client, session_usage.client),
              model = COALESCE(EXCLUDED.model, session_usage.model),
              repo = COALESCE(EXCLUDED.repo, session_usage.repo),
              started_at = LEAST(session_usage.started_at, EXCLUDED.started_at),
              ended_at = GREATEST(session_usage.ended_at, EXCLUDED.ended_at),
              {", ".join(f"{k} = GREATEST(session_usage.{k}, EXCLUDED.{k})" for k in COUNTERS)},
              updated_at = now()
            """,
            sid,
            cid,
            aid,
            p.actor,
            text("client"),
            text("model", 80),
            text("repo", 200),
            _when(data.get("started_at")),
            _when(data.get("ended_at")) or datetime.now(UTC),
            *counters.values(),
        )
    return {"session_id": sid, "recorded": True}


def _period(days: int, end: date | None) -> tuple[datetime, datetime]:
    last = end or datetime.now(UTC).date()
    start = last - timedelta(days=days - 1)
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(last.year, last.month, last.day, tzinfo=UTC) + timedelta(days=1),
    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


async def summary(days: int = 30, end: date | None = None) -> dict:
    """Painel: totais do período, por projeto e sessões com × sem uso do Concilium."""
    start, stop = _period(days, end)
    rows = await pool().fetch(
        """
        SELECT u.*, c.name AS collection FROM session_usage u LEFT JOIN collections c ON c.id = u.collection_id
        WHERE u.ended_at >= $1 AND u.ended_at < $2
        """,
        start,
        stop,
    )
    sessions = [dict(r) for r in rows]
    totals = {k: sum(s[k] for s in sessions) for k in COUNTERS}
    totals["sessions"] = len(sessions)
    totals["cost"] = round(sum(cost(s) for s in sessions))
    by_project: dict[str, dict] = {}
    for s in sessions:
        name = s["collection"] or "(sem projeto)"
        b = by_project.setdefault(name, {"name": name, "sessions": 0, "cost": 0.0, "requests": 0, "concilium_calls": 0})
        b["sessions"] += 1
        b["cost"] += cost(s)
        b["requests"] += s["requests"]
        b["concilium_calls"] += s["concilium_calls"]
    projects = sorted(by_project.values(), key=lambda b: -b["cost"])
    for b in projects:
        b["cost"] = round(b["cost"])
        b["avg_per_session"] = round(b["cost"] / b["sessions"]) if b["sessions"] else 0

    def group(items: list[dict]) -> dict:
        per_session = [cost(s) for s in items]
        per_request = [cost(s) / s["requests"] for s in items if s["requests"]]
        return {
            "sessions": len(items),
            "median_per_session": round(_median(per_session)),
            "median_per_request": round(_median(per_request)),
        }

    with_c = [s for s in sessions if s["concilium_calls"] > 0]
    without_c = [s for s in sessions if s["concilium_calls"] == 0]
    share = totals["concilium_result_tokens"] / max(1, totals["input_tokens"] + totals["cache_write_tokens"])
    return {
        "days": days,
        "totals": totals,
        "projects": projects,
        "with_concilium": group(with_c),
        "without_concilium": group(without_c),
        # quanto do contexto novo (entrada + gravação de cache) veio das respostas do Concilium
        "concilium_share": round(min(1.0, share), 4),
    }


async def card_usage(p: Principal, card_id: str) -> dict:
    """Custo real das sessões que mexeram no cartão (session_id do log do quadro)."""
    p.require("read")
    rows = await pool().fetch(
        """
        SELECT u.* FROM session_usage u
        WHERE u.session_id IN (
          SELECT DISTINCT session_id FROM board_events WHERE card_id = $1 AND session_id IS NOT NULL
        )
        """,
        parse_uuid(card_id, "card_id"),
    )
    sessions = [dict(r) for r in rows]
    return {
        "sessions": len(sessions),
        "cost": round(sum(cost(s) for s in sessions)),
        "output_tokens": sum(s["output_tokens"] for s in sessions),
        "requests": sum(s["requests"] for s in sessions),
    }
