"""Teste de paridade REST × MCP: toda tool MCP precisa de rota REST (ou exceção curada).

Falha quando uma tool nova nascer no mcp_server.py sem rota correspondente em api.py
e sem entrar na lista de exceções justificadas — o mapa abaixo é a fonte da verdade.
"""

from fastapi.routing import APIRoute

from mcp_rag_api.api import router as api_router
from mcp_rag_api.mcp_server import mcp

# tool MCP -> (método, path) da rota REST correspondente (api.py)
TOOL_ROUTES: dict[str, tuple[str, str]] = {
    # base de conhecimento
    "search_knowledge": ("POST", "/search"),
    "get_document": ("GET", "/documents/{document_id}"),
    "list_documents": ("GET", "/documents"),
    "list_collections": ("GET", "/collections"),
    "create_collection": ("POST", "/collections"),
    "add_document": ("POST", "/documents"),
    "update_document": ("PATCH", "/documents/{document_id}"),
    "upsert_document": ("PUT", "/documents/upsert"),
    "archive_document": ("DELETE", "/documents/{document_id}"),
    "document_history": ("GET", "/documents/{document_id}/versions"),
    # links
    "get_related": ("GET", "/documents/{document_id}/related"),
    "link_documents": ("POST", "/documents/{document_id}/links"),
    "unlink_documents": ("DELETE", "/links/{link_id}"),
    # agente: contexto e memória
    "load_agent": ("GET", "/agents/{slug}/context"),
    "recall": ("GET", "/agents/{slug}/memories"),
    "remember": ("POST", "/agents/{slug}/memories"),
    "forget": ("DELETE", "/agents/{slug}/memories/{memory_id}"),
    "save_session": ("POST", "/agents/{slug}/sessions"),
    "list_sessions": ("GET", "/agents/{slug}/sessions"),
    "list_tasks": ("GET", "/agents/{slug}/tasks"),
    "upsert_task": ("POST", "/agents/{slug}/tasks"),
    # gestão de agentes (agents:manage)
    "create_agent": ("POST", "/agents"),
    "update_agent": ("PATCH", "/agents/{slug}"),
    "set_agent_autonomy": ("PUT", "/agents/{slug}/autonomy"),
    "get_agent": ("GET", "/agents/{slug}"),
    "list_agents": ("GET", "/agents"),
    "clone_agent": ("POST", "/agents/{slug}/clone"),
    "archive_agent": ("DELETE", "/agents/{slug}"),
    "restore_agent_version": ("POST", "/agents/{slug}/restore/{version}"),
    "issue_agent_key": ("POST", "/agents/{slug}/keys"),
    "revoke_agent_key": ("DELETE", "/keys/{key_prefix}"),
    "list_agent_proposals": ("GET", "/agents/proposals"),
    "review_agent_update": ("POST", "/agents/proposals/{proposal_id}/review"),
    "add_agent_memory": ("POST", "/agents/{slug}/memories"),
    "add_agent_task": ("POST", "/agents/{slug}/tasks"),
}

# Tools que ficam só no MCP, com justificativa. Entrada nova aqui exige motivo real.
MCP_ONLY: dict[str, str] = {
    "propose_agent_update": "o agente propõe mudança no próprio perfil a partir da conversa; "
    "por API REST, o dono da chave altera direto com PATCH /agents/{slug}.",
}


def _rest_routes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in api_router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }


async def test_toda_tool_mcp_tem_rota_rest_ou_excecao() -> None:
    tools = {tool.name for tool in await mcp.list_tools()}
    cobertas = set(TOOL_ROUTES) | set(MCP_ONLY)
    sem_cobertura = tools - cobertas
    assert not sem_cobertura, (
        "tools MCP sem rota REST nem exceção: adicione a rota em api.py "
        f"ou uma exceção justificada em MCP_ONLY: {sorted(sem_cobertura)}"
    )
    extras = cobertas - tools
    assert not extras, (
        f"TOOL_ROUTES/MCP_ONLY mencionam tools que não existem no MCP (renomeadas ou removidas?): {sorted(extras)}"
    )


async def test_rotas_mapeadas_existem_no_app() -> None:
    rest = _rest_routes()
    faltantes = {tool: rota for tool, rota in TOOL_ROUTES.items() if rota not in rest}
    assert not faltantes, f"rotas REST do mapa não encontradas no app: {faltantes}"


def test_excecoes_mcp_only_tem_justificativa() -> None:
    for tool, motivo in MCP_ONLY.items():
        assert motivo.strip(), f"MCP_ONLY['{tool}'] precisa de justificativa não vazia"
        assert tool not in TOOL_ROUTES, f"'{tool}' não pode estar no mapa e nas exceções"
