"""Higiene do banco: o mesmo conjunto roda no `mcp-rag-api cleanup` e, periodicamente, no próprio servidor."""

from . import dash_auth, mfa, oauth
from .memory import cleanup_memories


async def cleanup_all() -> dict:
    """Memórias vencidas, sessões e desafios de 2FA da dash, e pedidos/tokens/clientes OAuth sem uso."""
    result = await cleanup_memories()
    result.update(await dash_auth.cleanup_sessions())
    result.update(await mfa.cleanup_challenges())
    result.update(await oauth.cleanup())
    return result
