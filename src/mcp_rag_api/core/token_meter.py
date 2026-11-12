"""Medidor de economia de tokens (plan-web-35).

Para cada leitura de um agente, compara os tokens entregues (`served`) com os tokens dos documentos
inteiros de onde a resposta saiu (`baseline`: o que ele leria sem o Concilium). Tudo é estimativa
(caracteres ÷ 4, ver core/tokens.py) e é dito assim na dash.

Fora do caminho da resposta: `record_documents` só agenda o trabalho; os contadores ficam numa fila
em memória e vão para `token_stats_daily` a cada FLUSH_SECONDS (e no desligamento). Se o processo
cair, perdem-se no máximo esses segundos de estatística. Nunca guarda consulta nem conteúdo.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

from ..config import get_settings
from ..db import pool, records
from ..logging_config import get_logger
from ..security import Principal
from .tokens import CHARS_PER_TOKEN, estimate_json_tokens

logger = get_logger()

FLUSH_SECONDS = 10.0

# (dia, coleção, agente, tool) -> [calls, served, baseline]
_Key = tuple[date, uuid.UUID | None, uuid.UUID | None, str]
_pending: dict[_Key, list[int]] = {}
_background: set[asyncio.Task] = set()
_flusher: asyncio.Task | None = None


def _today() -> date:
    return datetime.now(UTC).date()


def _add(key: _Key, calls: int, served: int, baseline: int) -> None:
    acc = _pending.setdefault(key, [0, 0, 0])
    acc[0] += calls
    acc[1] += served
    acc[2] += baseline


def served_by_document(items: list[dict]) -> dict[str, int]:
    """Tokens estimados de cada item da resposta, somados por documento."""
    out: dict[str, int] = {}
    for item in items:
        doc_id = item.get("document_id") or item.get("id")
        if doc_id:
            out[str(doc_id)] = out.get(str(doc_id), 0) + estimate_json_tokens(item)
    return out


def record_documents(tool: str, p: Principal, served_by_doc: dict[str, int]) -> None:
    """Registra uma leitura: tokens entregues por documento. O baseline (tamanho do documento
    inteiro) e a coleção de cada um saem do banco numa tarefa em segundo plano."""
    if not served_by_doc or not get_settings().token_meter_enabled:
        return
    task = asyncio.get_running_loop().create_task(_record_documents(tool, p.agent_id, dict(served_by_doc)))
    _background.add(task)
    task.add_done_callback(_background.discard)
    _ensure_flusher()


async def _record_documents(tool: str, agent_id: uuid.UUID | None, served_by_doc: dict[str, int]) -> None:
    try:
        ids = []
        for doc_id in served_by_doc:
            try:
                ids.append(uuid.UUID(doc_id))
            except ValueError:
                continue
        rows = await pool().fetch(
            "SELECT id, collection_id, length(title) + length(content) AS chars FROM documents WHERE id = ANY($1)",
            ids,
        )
        # uma chamada pode tocar várias coleções (busca sem filtro): cada uma recebe a sua parte
        per_collection: dict[uuid.UUID, list[int]] = {}
        for row in rows:
            served = served_by_doc.get(str(row["id"]), 0)
            baseline = -(-row["chars"] // CHARS_PER_TOKEN)
            acc = per_collection.setdefault(row["collection_id"], [0, 0])
            acc[0] += served
            acc[1] += max(baseline, served)  # economia nunca negativa: ler em partes não "custa" mais
        day = _today()
        for i, (collection_id, (served, baseline)) in enumerate(per_collection.items()):
            _add((day, collection_id, agent_id, tool), 1 if i == 0 else 0, served, baseline)
    except Exception:  # estatística nunca derruba nada
        logger.exception("token_meter: falha ao registrar leitura de %s", tool)


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is None or _flusher.done():
        _flusher = asyncio.get_running_loop().create_task(_flush_loop())


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(FLUSH_SECONDS)
        await flush()


async def flush() -> None:
    """Grava os contadores pendentes (soma com o que já existe no dia)."""
    if _background:
        await asyncio.gather(*list(_background), return_exceptions=True)
    if not _pending:
        return
    batch = [(day, cid, aid, tool, c, s, b) for (day, cid, aid, tool), (c, s, b) in _pending.items()]
    _pending.clear()
    try:
        await pool().executemany(
            """
            INSERT INTO token_stats_daily (day, collection_id, agent_id, tool, calls, served, baseline)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT ON CONSTRAINT token_stats_daily_key DO UPDATE
               SET calls = token_stats_daily.calls + EXCLUDED.calls,
                   served = token_stats_daily.served + EXCLUDED.served,
                   baseline = token_stats_daily.baseline + EXCLUDED.baseline
            """,
            batch,
        )
    except Exception:
        logger.exception("token_meter: falha ao gravar %d linhas de estatística", len(batch))


async def shutdown() -> None:
    global _flusher
    if _flusher is not None:
        _flusher.cancel()
        _flusher = None
    await flush()


async def summary(days: int, end: date | None = None) -> dict:
    """Bloco "tokens" do Painel: totais, série diária e quebra por projeto e por agente, nos `days` dias
    que terminam em `end` (padrão: hoje)."""
    async with pool().acquire() as conn:
        if end is None:
            end = await conn.fetchval("SELECT current_date")
        totals = await conn.fetchrow(
            """
            SELECT coalesce(sum(calls), 0) AS calls, coalesce(sum(served), 0) AS served,
                   coalesce(sum(baseline), 0) AS baseline
            FROM token_stats_daily WHERE day BETWEEN $2::date - ($1 - 1) AND $2::date
            """,
            days,
            end,
        )
        daily = await conn.fetch(
            """
            WITH days AS (
                SELECT d::date AS day
                FROM generate_series($2::date - ($1 - 1), $2::date, interval '1 day') AS d
            ),
            sums AS (
                SELECT day, sum(served) AS served, sum(baseline) AS baseline
                FROM token_stats_daily WHERE day BETWEEN $2::date - ($1 - 1) AND $2::date GROUP BY day
            )
            SELECT days.day, coalesce(sums.served, 0) AS served, coalesce(sums.baseline, 0) AS baseline
            FROM days LEFT JOIN sums USING (day) ORDER BY day
            """,
            days,
            end,
        )
        by_collection = await conn.fetch(
            """
            SELECT coalesce(c.name, '—') AS name, sum(t.calls) AS calls, sum(t.served) AS served,
                   sum(t.baseline) AS baseline
            FROM token_stats_daily t LEFT JOIN collections c ON c.id = t.collection_id
            WHERE t.day BETWEEN $2::date - ($1 - 1) AND $2::date
            GROUP BY 1 ORDER BY sum(t.baseline) - sum(t.served) DESC LIMIT 20
            """,
            days,
            end,
        )
        by_agent = await conn.fetch(
            """
            SELECT a.slug, a.name, sum(t.calls) AS calls, sum(t.served) AS served, sum(t.baseline) AS baseline
            FROM token_stats_daily t JOIN agents a ON a.id = t.agent_id
            WHERE t.day BETWEEN $2::date - ($1 - 1) AND $2::date
            GROUP BY a.slug, a.name ORDER BY sum(t.baseline) - sum(t.served) DESC LIMIT 20
            """,
            days,
            end,
        )

    def ints(rows: list[dict]) -> list[dict]:
        return [{k: int(v) if k in ("calls", "served", "baseline") else v for k, v in r.items()} for r in rows]

    total = {k: int(v) for k, v in dict(totals).items()} if totals else {"calls": 0, "served": 0, "baseline": 0}
    return {
        **total,
        "saved": total["baseline"] - total["served"],
        "daily": ints(records(daily)),
        "by_collection": ints(records(by_collection)),
        "by_agent": ints(records(by_agent)),
    }
