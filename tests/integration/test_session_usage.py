"""Gasto real das sessões (plan-web-38): gravação, resumo do Painel e custo do cartão."""

import uuid

import httpx
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import boards, documents, usage
from mcp_rag_api.main import app
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import PermissionDenied, Principal, create_api_key

COL = "usage-38"


@pytest.fixture(scope="module", autouse=True)
async def setup():
    await documents.create_collection(ADMIN, COL)


def _s(sid, **kw):
    base = {
        "session_id": sid,
        "collection": COL,
        "requests": 10,
        "input_tokens": 100,
        "output_tokens": 1000,
        "cache_write_tokens": 2000,
        "cache_read_tokens": 100000,
    }
    return base | kw


async def test_record_is_cumulative_and_owned():
    reader = Principal(actor="key:leitor-38", scopes=frozenset({"read"}))
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    await usage.record_session(reader, _s(sid))
    await usage.record_session(reader, _s(sid, requests=4, output_tokens=10))  # envio atrasado não regride
    await usage.record_session(reader, _s(sid, requests=12, output_tokens=1500))
    row = await db.pool().fetchrow("SELECT * FROM session_usage WHERE session_id = $1", sid)
    assert row["requests"] == 12 and row["output_tokens"] == 1500
    with pytest.raises(PermissionDenied):
        await usage.record_session(Principal(actor="key:outro", scopes=frozenset({"read"})), _s(sid))


async def test_summary_with_and_without_concilium():
    p = Principal(actor="key:resumo-38", scopes=frozenset({"read"}))
    await usage.record_session(p, _s(f"com-{uuid.uuid4().hex[:8]}", concilium_calls=5, concilium_result_tokens=200))
    await usage.record_session(p, _s(f"sem-{uuid.uuid4().hex[:8]}", requests=10, output_tokens=3000))
    s = await usage.summary(30)
    proj = next(x for x in s["projects"] if x["name"] == COL)
    assert proj["sessions"] >= 2 and proj["cost"] > 0
    assert s["with_concilium"]["sessions"] >= 1 and s["without_concilium"]["sessions"] >= 1
    expected = 100 + 2000 * 1.25 + 100000 * 0.1 + 1000 * 5
    assert usage.cost(_s("x")) == expected
    assert 0 < s["concilium_share"] <= 1


async def test_card_cost_from_sessions_in_board_log():
    board = await boards.create_board(ADMIN, COL, f"Q {uuid.uuid4().hex[:5]}")
    card = await boards.create_card(ADMIN, board["id"], "Com custo")
    agente = Principal(actor="agent:dev-38", scopes=frozenset({"read", "write"}), agent_slug="dev-38")
    sid = f"cartao-{uuid.uuid4().hex[:8]}"
    await boards.start_card(agente, card["id"], origin=boards.Origin.of("Claude Code", sid))
    await usage.record_session(Principal(actor="key:c38", scopes=frozenset({"read"})), _s(sid))
    got = await usage.card_usage(ADMIN, card["id"])
    assert got["sessions"] == 1 and got["cost"] == round(usage.cost(_s(sid))) and got["requests"] == 10


async def test_rest_route():
    async with db.pool().acquire() as conn:
        key = (await create_api_key(conn, label="usage-rest", scopes=["read"]))["api_key"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"Authorization": f"Bearer {key}"}
    ) as c:
        r = await c.post("/usage/sessions", json=_s(f"rest-{uuid.uuid4().hex[:8]}"))
        assert r.status_code == 200, r.text
        assert (await c.post("/usage/sessions", json=_s("curta"))).status_code == 422
        assert (
            await c.post("/usage/sessions", json=_s(f"neg-{uuid.uuid4().hex[:8]}", output_tokens=-1))
        ).status_code == 422
