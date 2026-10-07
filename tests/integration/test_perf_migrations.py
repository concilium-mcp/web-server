"""Performance de queries e migrações (plan-web-09): /insights agregado, índice de
document_versions, advisory lock das migrações e throttle de last_used_at.

Coleção com prefixo "perf-" (o banco é compartilhado entre os módulos de integração).
"""

import asyncio

import httpx
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import dash_auth, documents
from mcp_rag_api.main import app
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import PermissionDenied, create_api_key, hash_key, resolve_key

# Query antiga do /insights (uma subconsulta correlata por dia), mantida como referência de
# equivalência: o endpoint otimizado (agregação única GROUP BY) tem que devolver exatamente o mesmo.
_NAIVE_EDITS_SQL = """
SELECT d::date AS day,
       (SELECT count(*) FROM document_versions v WHERE v.created_at::date = d::date) AS edits,
       (SELECT count(*) FROM document_versions v
         WHERE v.created_at::date = d::date AND v.version = 1) AS created
FROM generate_series(current_date - ($1 - 1), current_date, interval '1 day') AS d
ORDER BY day
"""


@pytest.fixture(scope="module")
async def viewer():
    await dash_auth.create_user("perf-leitora", "senha-segura-123", "viewer")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dash.test")
    resp = await client.post("/dash/api/auth/login", json={"username": "perf-leitora", "password": "senha-segura-123"})
    assert resp.status_code == 200
    yield client
    await client.aclose()


async def _backdate(document_id: str, version: int, days_ago: int) -> None:
    async with db.pool().acquire() as conn:
        await conn.execute(
            "UPDATE document_versions SET created_at = now() - make_interval(days => $3), "
            "changed_by = 'dash:perf-seeder' "
            "WHERE document_id = $1::uuid AND version = $2",
            document_id,
            version,
            days_ago,
        )


async def test_insights_activity_matches_naive_query(viewer):
    """Seeds em dias variados e comparação do activity/contribuidores do endpoint com a query
    ingênua de referência (subconsultas correlatas): o resultado tem que ser idêntico."""
    await documents.create_collection(ADMIN, "perf-col")
    # doc A: v1+v2 há 1 dia (2 edits, 1 created), v3 hoje (1 edit)
    a = await documents.add_document(ADMIN, "perf-col", "Perf alfa", "conteúdo alfa v1")
    a_id = a["document_id"]
    await documents.update_document(ADMIN, a_id, "ajuste alfa", content="conteúdo alfa v2")
    await documents.update_document(ADMIN, a_id, "ajuste alfa 2", content="conteúdo alfa v3")
    await _backdate(a_id, 1, 1)
    await _backdate(a_id, 2, 1)
    # doc B: v1 há 2 dias (1 edit, 1 created)
    b = await documents.add_document(ADMIN, "perf-col", "Perf beta", "conteúdo beta v1")
    await _backdate(b["document_id"], 1, 2)
    # doc C: v1 há 35 dias — fora do período de 30 dias
    c = await documents.add_document(ADMIN, "perf-col", "Perf gama", "conteúdo gama v1")
    await _backdate(c["document_id"], 1, 35)

    days = 30
    data = (await viewer.get("/dash/api/insights", params={"days": days})).json()
    assert data["days"] == days and len(data["activity"]) == days

    async with db.pool().acquire() as conn:
        reference = await conn.fetch(_NAIVE_EDITS_SQL, days)
    expected = [(str(r["day"]), r["edits"], r["created"]) for r in reference]
    got = [(a["day"], a["edits"], a["created"]) for a in data["activity"]]
    assert got == expected

    # contribuidores no período: a seed garante um ator exclusivo com contagem exata;
    # 'dev' acumula versões de outros módulos (banco compartilhado), então só o mínimo se afirma
    contributors = {c["actor"]: c["edits"] for c in data["contributors"]}
    assert contributors["dash:perf-seeder"] == 3
    assert contributors["dev"] >= 1


async def test_versions_created_at_index_exists():
    async with db.pool().acquire() as conn:
        names = [
            r["indexname"]
            for r in await conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'document_versions'")
        ]
    assert "idx_document_versions_created_at" in names  # migração 005


async def test_run_migrations_concurrent_applies_once():
    """Três runners simultânes com a 005 pendente: o advisory lock serializa e exatamente um aplica."""
    async with db.pool().acquire() as conn:
        await conn.execute("DELETE FROM schema_migrations WHERE name = '005_versions_created_at_idx.sql'")
    results = await asyncio.gather(*(db.run_migrations() for _ in range(3)))
    assert sum("005_versions_created_at_idx.sql" in applied for applied in results) == 1
    async with db.pool().acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM schema_migrations")
    assert total == 5  # 001..005, sem duplicatas


async def test_resolve_key_throttles_last_used_at():
    """last_used_at só regrava após 60s; a validade continua sem cache (revogação imediata)."""
    async with db.pool().acquire() as conn:
        key = await create_api_key(conn, label="perf-throttle", scopes=["read"])
        raw = key["api_key"]
        digest = hash_key(raw)

        await resolve_key(conn, raw)
        first = await conn.fetchval("SELECT last_used_at FROM api_keys WHERE key_hash = $1", digest)
        await resolve_key(conn, raw)  # dentro da janela de 60s: não pode regravar
        second = await conn.fetchval("SELECT last_used_at FROM api_keys WHERE key_hash = $1", digest)
        assert first == second

        await conn.execute(
            "UPDATE api_keys SET last_used_at = now() - interval '2 minutes' WHERE key_hash = $1", digest
        )
        await resolve_key(conn, raw)  # fora da janela: regrava
        third = await conn.fetchval("SELECT last_used_at FROM api_keys WHERE key_hash = $1", digest)
        assert third > second

        await conn.execute("UPDATE api_keys SET revoked_at = now() WHERE key_hash = $1", digest)
        with pytest.raises(PermissionDenied, match="revogada"):
            await resolve_key(conn, raw)
