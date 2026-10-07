"""Pool de conexões asyncpg + migrações SQL simples (arquivos NNN_*.sql aplicados em ordem).

Regras das migrações: TODA migração nova usa IF NOT EXISTS / IF EXISTS onde aplicável;
migrações já aplicadas (registradas em schema_migrations) não se editam — criar arquivo novo.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from .config import default_migrations_dir, get_settings

_pool: asyncpg.Pool | None = None

# Lock global de migração (advisory): dois processos subindo juntos (rolling deploy, --workers > 1)
# serializam em vez de aplicar a mesma migração em corrida.
_MIGRATION_LOCK_ID = 727_474


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def run_migrations(database_url: str | None = None) -> list[str]:
    settings = get_settings()
    conn = await asyncpg.connect(database_url or settings.database_url)
    applied: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
            migrations_dir = settings.migrations_dir or default_migrations_dir()
            for path in sorted(migrations_dir.glob("*.sql")):
                if path.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
                applied.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)
    finally:
        await conn.close()
    return applied


async def init_pool(database_url: str | None = None) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            database_url or get_settings().database_url, min_size=1, max_size=10, init=_init_connection
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def is_ready() -> bool:
    return _pool is not None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool do banco não inicializado")
    return _pool


@asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    await run_migrations()
    await init_pool()
    try:
        yield
    finally:
        await close_pool()


def record(row: asyncpg.Record | None) -> dict[str, Any] | None:
    """Converte um Record em dict serializável (UUID/datetime viram str)."""
    if row is None:
        return None
    out: dict[str, Any] = {}
    for k, v in dict(row).items():
        if k == "embedding":
            continue
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, uuid.UUID):
            v = str(v)
        out[k] = v
    return out


def records(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [record(r) for r in rows]  # type: ignore[misc]


async def audit(conn: asyncpg.Connection, actor: str, action: str, target: str | None, **details: Any) -> None:
    await conn.execute(
        "INSERT INTO audit_log (actor, action, target, details) VALUES ($1, $2, $3, $4)",
        actor,
        action,
        target,
        details,
    )
