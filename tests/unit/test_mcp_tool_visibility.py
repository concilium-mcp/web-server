"""Visibilidade das tools do MCP por conexão: cada conexão só vê o que consegue usar."""

import uuid
from types import SimpleNamespace

import pytest

from mcp_rag_api.mcp_server import AGENT_CONTEXT_TOOLS, SELF_ONLY_TOOLS, TOOL_SCOPES, mcp, tool_visible
from mcp_rag_api.security import Principal

PERSON_READ = Principal(actor="dash:viewer", scopes=frozenset({"read"}))
PERSON_WRITE = Principal(actor="dash:editor", scopes=frozenset({"read", "write"}))
PERSON_MANAGER = Principal(actor="dash:admin", scopes=frozenset({"read", "write", "agents:manage"}))
AGENT = Principal(actor="agent:larica", scopes=frozenset({"read", "write"}), agent_id=uuid.uuid4(), agent_slug="larica")
ADMIN_KEY = Principal(actor="key:admin", scopes=frozenset({"admin"}))


async def _listed(p: Principal) -> dict[str, str]:
    # mesmo caminho da produção: o portão (RequireBearer) põe o Principal no scope da requisição
    ctx = SimpleNamespace(request=SimpleNamespace(scope={"kb.principal": p}, headers={}))
    result = await mcp._handle_list_tools(ctx, None)
    return {t.name: t.description or "" for t in result.tools}


async def test_every_registered_tool_is_classified():
    # tool nova sem classificação apareceria para todo mundo: o teste obriga a decidir
    registered = {t.name for t in await mcp.list_tools()}
    assert registered == set(TOOL_SCOPES), sorted(registered ^ set(TOOL_SCOPES))
    assert AGENT_CONTEXT_TOOLS | SELF_ONLY_TOOLS <= registered


async def test_person_without_agent_does_not_see_agent_tools():
    tools = await _listed(PERSON_WRITE)
    assert "search_knowledge" in tools and "add_document" in tools
    assert not (AGENT_CONTEXT_TOOLS | SELF_ONLY_TOOLS) & set(tools)  # o caso do "recall" que falhava
    assert "create_agent" not in tools and "delete_collection" not in tools


async def test_read_only_sees_no_write_tools():
    tools = await _listed(PERSON_READ)
    assert "search_knowledge" in tools
    assert not {name for name, scope in TOOL_SCOPES.items() if scope != "read"} & set(tools)


async def test_manager_sees_agent_tools_with_agent_slug_hint():
    tools = await _listed(PERSON_MANAGER)
    assert AGENT_CONTEXT_TOOLS <= set(tools) and "propose_agent_update" not in tools
    assert "informe agent_slug" in tools["recall"]
    assert "create_agent" in tools and "delete_collection" not in tools  # admin != agents:manage


async def test_agent_sees_its_own_tools_without_hint():
    tools = await _listed(AGENT)
    assert AGENT_CONTEXT_TOOLS | SELF_ONLY_TOOLS <= set(tools)
    assert "informe agent_slug" not in tools["recall"]
    assert "create_agent" not in tools  # agente comum não gerencia agentes


async def test_admin_key_sees_everything_but_self_only():
    tools = await _listed(ADMIN_KEY)
    assert set(tools) == set(TOOL_SCOPES) - SELF_ONLY_TOOLS


@pytest.mark.parametrize("p", [PERSON_READ, PERSON_WRITE, PERSON_MANAGER, AGENT, ADMIN_KEY])
def test_visibility_matches_listing_rule(p):
    for name in TOOL_SCOPES:
        if name in AGENT_CONTEXT_TOOLS and p.agent_slug is None and not p.is_manager:
            assert not tool_visible(name, p)
