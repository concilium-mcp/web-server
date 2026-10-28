"""Registro de agentes sem escalonamento: quem tem agents:manage sem admin não vira admin
(chave/restauração de agente elevado) nem sai do próprio alcance de coleções."""

import pytest

from mcp_rag_api import db
from mcp_rag_api.core import agents, documents
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import PermissionDenied, Principal, resolve_key

MANAGER = Principal(actor="key:manager", scopes=frozenset({"agents:manage"}))
# mesmo formato do Principal de uma conexão OAuth de admin com "Algumas pastas"
SCOPED_MANAGER = Principal(
    actor="dash:oauth-admin",
    scopes=frozenset({"read", "write", "agents:manage"}),
    allowed_collections=("sec-a",),
)


async def _agent(slug: str, scopes: list[str], collections: list[str] | None = None) -> dict:
    return await agents.create_agent(ADMIN, slug, slug, "prompt", allowed_collections=collections, scopes=scopes)


async def test_manager_cannot_issue_key_for_elevated_agent():
    await _agent("sec-ops-admin", ["admin"])
    await _agent("sec-plain", ["read", "write"])

    with pytest.raises(PermissionDenied):
        await agents.issue_agent_key(MANAGER, "sec-ops-admin")
    assert (await agents.issue_agent_key(MANAGER, "sec-plain"))["api_key"]
    assert (await agents.issue_agent_key(ADMIN, "sec-ops-admin"))["api_key"]


async def test_manager_cannot_restore_elevated_version():
    await _agent("sec-restore", ["admin"])  # v1 com admin
    await agents.update_agent(ADMIN, "sec-restore", "rebaixa", scopes=["read"])  # v2 só leitura

    with pytest.raises(PermissionDenied):
        await agents.restore_agent_version(MANAGER, "sec-restore", 1)
    restored = await agents.restore_agent_version(ADMIN, "sec-restore", 1)
    assert restored["scopes"] == ["admin"]


async def test_manager_agent_cannot_touch_own_profile():
    created = await _agent("sec-self", ["admin"])  # v1 com admin
    await agents.update_agent(ADMIN, "sec-self", "vira gestor", scopes=["agents:manage"])  # v2
    async with db.pool().acquire() as conn:
        me = await resolve_key(conn, created["api_key"])
    assert me.agent_slug == "sec-self" and me.scopes == {"agents:manage"}

    with pytest.raises(PermissionDenied):
        await agents.restore_agent_version(me, "sec-self", 1)
    with pytest.raises(PermissionDenied):
        await agents.update_agent(me, "sec-self", "auto", system_prompt="faça tudo")
    with pytest.raises(PermissionDenied):
        await agents.issue_agent_key(me, "sec-self")


async def test_scoped_manager_cannot_widen_collections():
    for name in ("sec-a", "sec-b"):
        await documents.create_collection(ADMIN, name)

    for cols in (None, [], ["sec-b"], ["sec-a", "sec-b"]):
        with pytest.raises(PermissionDenied):
            await agents.create_agent(SCOPED_MANAGER, "sec-wide", "x", "p", allowed_collections=cols)
    narrow = await agents.create_agent(SCOPED_MANAGER, "sec-narrow", "x", "p", allowed_collections=["sec-a"])
    assert narrow["agent"]["allowed_collections"] == ["sec-a"]

    with pytest.raises(PermissionDenied):
        await agents.update_agent(SCOPED_MANAGER, "sec-narrow", "ampliar", allowed_collections=[])
    with pytest.raises(PermissionDenied):
        await agents.clone_agent(SCOPED_MANAGER, "sec-narrow", "sec-clone", "x", {"allowed_collections": []})
    updated = await agents.update_agent(SCOPED_MANAGER, "sec-narrow", "ajuste", system_prompt="p2")
    assert updated["allowed_collections"] == ["sec-a"]

    # agente sem restrição (todas as coleções) fica fora do alcance de quem é restrito
    await _agent("sec-unscoped", ["read"])
    with pytest.raises(PermissionDenied):
        await agents.issue_agent_key(SCOPED_MANAGER, "sec-unscoped")
    with pytest.raises(PermissionDenied):
        await agents.update_agent(SCOPED_MANAGER, "sec-unscoped", "x", system_prompt="y")
    with pytest.raises(PermissionDenied):
        await agents.archive_agent(SCOPED_MANAGER, "sec-unscoped", "x")
