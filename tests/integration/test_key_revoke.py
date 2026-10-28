"""revoke_agent_key (API/MCP): agents:manage revoga chave de agente comum, nunca chave humana
nem chave de agente com escopo elevado."""

import pytest

from mcp_rag_api import db
from mcp_rag_api.core import agents, documents
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import PermissionDenied, Principal, create_api_key, resolve_key

MANAGER = Principal(actor="key:manager", scopes=frozenset({"agents:manage"}))


async def _still_valid(raw: str) -> bool:
    async with db.pool().acquire() as conn:
        try:
            await resolve_key(conn, raw)
        except PermissionDenied:
            return False
    return True


async def test_manager_cannot_revoke_human_key():
    async with db.pool().acquire() as conn:
        human = await create_api_key(conn, label="admin humano", scopes=["admin"])

    with pytest.raises(PermissionDenied):
        await agents.revoke_agent_key(MANAGER, human["prefix"])
    assert await _still_valid(human["api_key"])

    assert (await agents.revoke_agent_key(ADMIN, human["prefix"]))["revoked"] == 1
    assert not await _still_valid(human["api_key"])


async def test_manager_revokes_only_plain_agent_keys():
    plain = await agents.create_agent(ADMIN, "rev-plain", "Comum", "p", scopes=["read"])
    elevated = await agents.create_agent(ADMIN, "rev-admin", "Elevado", "p", scopes=["admin"])
    plain_prefix = plain["api_key"][:12]
    elevated_prefix = elevated["api_key"][:12]

    with pytest.raises(PermissionDenied):
        await agents.revoke_agent_key(MANAGER, elevated_prefix)
    assert await _still_valid(elevated["api_key"])

    assert (await agents.revoke_agent_key(MANAGER, plain_prefix))["revoked"] == 1
    assert not await _still_valid(plain["api_key"])


async def test_scoped_manager_revokes_only_keys_within_its_collections():
    scoped = Principal(
        actor="dash:oauth-admin",
        scopes=frozenset({"read", "write", "agents:manage"}),
        allowed_collections=("rev-a",),
    )
    for name in ("rev-a", "rev-b"):
        await documents.create_collection(ADMIN, name)
    inside = await agents.create_agent(ADMIN, "rev-in", "Dentro", "p", allowed_collections=["rev-a"], scopes=["read"])
    outside = await agents.create_agent(ADMIN, "rev-out", "Fora", "p", allowed_collections=["rev-b"], scopes=["read"])

    with pytest.raises(PermissionDenied):
        await agents.revoke_agent_key(scoped, outside["api_key"][:12])
    assert await _still_valid(outside["api_key"])

    assert (await agents.revoke_agent_key(scoped, inside["api_key"][:12]))["revoked"] == 1
    assert not await _still_valid(inside["api_key"])
