"""Medidor de economia de tokens (plan-web-35): agregação diária, baseline e resumo do Painel."""

import pytest

from mcp_rag_api import db, mcp_server
from mcp_rag_api.config import get_settings
from mcp_rag_api.core import documents, token_meter
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN

COLLECTION = "meter-w35"
TEXT = "reuniao conselho orcamento ata " * 400  # ~12 mil caracteres ≈ 3 mil tokens


@pytest.fixture(scope="module")
async def doc_id():
    await documents.create_collection(ADMIN, COLLECTION)
    r = await documents.add_document(ADMIN, COLLECTION, "Ata grande", TEXT, force=True)
    return r["document_id"]


@pytest.fixture(autouse=True)
async def _clean():
    await token_meter.flush()
    await db.pool().execute("DELETE FROM token_stats_daily")
    yield
    get_settings.cache_clear()


async def _rows():
    return await db.pool().fetch(
        "SELECT t.tool, t.calls, t.served, t.baseline FROM token_stats_daily t "
        "JOIN collections c ON c.id = t.collection_id WHERE c.name = $1 ORDER BY tool",
        COLLECTION,
    )


async def test_two_reads_same_day_add_up(doc_id):
    token_meter.record_documents("get_document", ADMIN, {doc_id: 100})
    token_meter.record_documents("get_document", ADMIN, {doc_id: 50})
    await token_meter.flush()
    [row] = await _rows()
    expected_baseline = -(-(len("Ata grande") + len(TEXT.strip())) // 4)
    assert (row["calls"], row["served"]) == (2, 150)
    assert abs(row["baseline"] - 2 * expected_baseline) <= 4  # o core normaliza espaços do conteúdo


async def test_search_counts_each_document_once_and_never_negative(doc_id):
    out = await mcp_server.search_knowledge(_ctx(), "reuniao conselho", [COLLECTION], detail="full")
    assert len(out) >= 2  # vários trechos do mesmo documento
    await token_meter.flush()
    [row] = await _rows()
    assert row["calls"] == 1 and row["baseline"] >= row["served"] > 0


async def test_disabled_records_nothing(doc_id, monkeypatch):
    monkeypatch.setenv("TOKEN_METER_ENABLED", "false")
    get_settings.cache_clear()
    token_meter.record_documents("get_document", ADMIN, {doc_id: 10})
    await token_meter.flush()
    assert await _rows() == []


async def test_bad_ids_and_unknown_docs_do_not_break(doc_id):
    token_meter.record_documents("get_document", ADMIN, {"nao-e-uuid": 5, "00000000-0000-0000-0000-000000000000": 5})
    await token_meter.flush()
    assert await _rows() == []


async def test_summary_for_overview(doc_id):
    await mcp_server.get_document(_ctx(), doc_id, max_tokens=200)
    await token_meter.flush()
    data = await token_meter.summary(7)
    assert data["calls"] >= 1 and data["saved"] > 0
    assert len(data["daily"]) == 7 and data["daily"][-1]["served"] > 0
    project = next(c for c in data["by_collection"] if c["name"] == COLLECTION)
    assert project["baseline"] > project["served"]
    assert data["by_agent"] == []  # DEV_PRINCIPAL não é agente


def _ctx():
    from types import SimpleNamespace

    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(scope={"kb.principal": ADMIN})))
