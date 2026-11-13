"""Tokens estimados do catálogo de tools MCP por tipo de conexão (plan-web-33).

Uso: uv run python scripts/measure_tool_catalog.py
Mede o que o cliente recebe no tools/list (nome, descrição e schemas), mais as instruções do servidor.
É estimativa (caracteres ÷ 4), a mesma régua de core/tokens.py.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

from mcp_rag_api.core.tokens import estimate_tokens
from mcp_rag_api.mcp_server import INSTRUCTIONS, mcp
from mcp_rag_api.security import Principal

PROFILES = {
    "leitura (read)": Principal(actor="dash:viewer", scopes=frozenset({"read"})),
    "agente (read, write)": Principal(
        actor="agent:demo", scopes=frozenset({"read", "write"}), agent_id=uuid.uuid4(), agent_slug="demo"
    ),
    "dono (admin)": Principal(actor="key:admin", scopes=frozenset({"admin"})),
}


async def main() -> None:
    print(f"instruções do servidor: ~{estimate_tokens(INSTRUCTIONS)} tokens (uma vez por conexão)")
    for label, principal in PROFILES.items():
        for profile in ("", "dev"):
            if profile and label != "agente (read, write)":
                continue
            req = SimpleNamespace(scope={"kb.principal": principal}, headers={}, query_params={"tools": profile})
            result = await mcp._handle_list_tools(SimpleNamespace(request=req), None)
            payload = json.dumps(
                [t.model_dump(by_alias=True, exclude_none=True) for t in result.tools], ensure_ascii=False
            )
            nome = f"{label} · ?tools={profile}" if profile else label
            print(f"{nome}: {len(result.tools)} tools, ~{estimate_tokens(payload)} tokens")


if __name__ == "__main__":
    asyncio.run(main())
