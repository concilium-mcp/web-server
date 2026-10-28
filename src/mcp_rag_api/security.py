"""Chaves de API, identidade (Principal) e escopos.

Escopos:
  read           consultar a base e o próprio agente
  write          inserir/atualizar documentos (implica read)
  agents:manage  cadastrar/configurar agentes, aprovar propostas, ligar autonomia
  admin          tudo
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

VALID_SCOPES = {"read", "write", "agents:manage", "admin"}
_IMPLIES = {"write": {"read"}, "agents:manage": {"read"}}
KEY_PREFIX = "kb_sk_"
# OAuth do MCP (plan-web-17): tokens opacos emitidos pela tela "Autorizar"
OAUTH_ACCESS_PREFIX = "kb_at_"
OAUTH_REFRESH_PREFIX = "kb_rt_"
# teto de escopos que um usuário da dash pode conceder via OAuth, por papel (nunca "admin")
OAUTH_ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    "viewer": ("read",),
    "editor": ("read", "write"),
    "admin": ("read", "write", "agents:manage"),
}


class KBError(Exception):
    """Erro esperado, com mensagem para o agente/usuário."""


class PermissionDenied(KBError):
    pass


class NotFound(KBError):
    pass


class VersionConflict(KBError):
    """Conflito de trava otimista: o recurso mudou desde a versão lida pelo cliente."""

    def __init__(self, message: str, *, current_version: int, actor: str | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.actor = actor  # quem gravou a versão atual, quando conhecido


@dataclass(frozen=True)
class Principal:
    actor: str
    scopes: frozenset[str]
    agent_id: UUID | None = None
    agent_slug: str | None = None
    allowed_collections: tuple[str, ...] = field(default_factory=tuple)  # vazio = todas

    def has(self, scope: str) -> bool:
        if "admin" in self.scopes or scope in self.scopes:
            return True
        return any(scope in _IMPLIES.get(s, ()) for s in self.scopes)

    def require(self, scope: str) -> None:
        if not self.has(scope):
            raise PermissionDenied(f"Esta chave não tem o escopo '{scope}'.")

    @property
    def is_manager(self) -> bool:
        return self.has("agents:manage")

    def can_access_collection(self, name: str) -> bool:
        return not self.allowed_collections or name in self.allowed_collections

    def require_collection(self, name: str) -> None:
        if not self.can_access_collection(name):
            raise PermissionDenied(f"Sem acesso à coleção '{name}'.")

    def collection_filter(self, requested: list[str] | None) -> list[str] | None:
        """Coleções efetivas para uma busca: pedido ∩ permitido (None = sem filtro)."""
        if requested:
            for name in requested:
                self.require_collection(name)
            return requested
        return list(self.allowed_collections) or None


DEV_PRINCIPAL = Principal(actor="dev", scopes=frozenset({"admin"}))


def validate_scopes(scopes: list[str]) -> list[str]:
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise KBError(f"Escopos inválidos: {sorted(invalid)}. Válidos: {sorted(VALID_SCOPES)}")
    return sorted(set(scopes))


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def create_api_key(
    conn: asyncpg.Connection, *, label: str, scopes: list[str], agent_id: UUID | None = None
) -> dict:
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    row = await conn.fetchrow(
        "INSERT INTO api_keys (key_hash, prefix, label, agent_id, scopes) VALUES ($1, $2, $3, $4, $5) "
        "RETURNING id, prefix, created_at",
        hash_key(raw),
        raw[:12],
        label,
        agent_id,
        validate_scopes(scopes),
    )
    return {"key_id": str(row["id"]), "prefix": row["prefix"], "api_key": raw}


async def resolve_key(conn: asyncpg.Connection, raw: str) -> Principal:
    row = await conn.fetchrow(
        """
        SELECT k.id, k.prefix, k.scopes AS key_scopes, k.agent_id,
               a.slug, a.scopes AS agent_scopes, a.allowed_collections, a.status AS agent_status
        FROM api_keys k LEFT JOIN agents a ON a.id = k.agent_id
        WHERE k.key_hash = $1 AND k.revoked_at IS NULL
        """,
        hash_key(raw),
    )
    if row is None:
        raise PermissionDenied("Chave de API inválida ou revogada.")
    # Throttle do last_used_at (a coluna é informativa; o portão do /mcp + cada tool resolviam a
    # mesma chave 2× por request). A validade NÃO é cacheada: o SELECT acima roda toda vez, então
    # a revogação continua imediata.
    await conn.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE id = $1 "
        "AND (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')",
        row["id"],
    )
    if row["agent_id"] is None:
        return Principal(actor=f"key:{row['prefix']}", scopes=frozenset(row["key_scopes"]))
    if row["agent_status"] != "active":
        raise PermissionDenied("O agente desta chave está arquivado.")
    return Principal(
        actor=f"agent:{row['slug']}",
        scopes=frozenset(row["agent_scopes"]),
        agent_id=row["agent_id"],
        agent_slug=row["slug"],
        allowed_collections=tuple(row["allowed_collections"]),
    )


async def resolve_oauth_token(conn: asyncpg.Connection, raw: str) -> Principal:
    """Access token OAuth → Principal. Revalida a cada request: token/conexão revogados ou expirados,
    usuário desativado, papel rebaixado abaixo do concedido ou agente arquivado invalidam na hora."""
    row = await conn.fetchrow(
        """
        SELECT t.expires_at, t.revoked_at, t.scopes,
               g.id AS grant_id, g.revoked_at AS grant_revoked_at, g.allowed_collections, g.agent_id,
               g.last_used_at, u.username, u.role, u.disabled_at,
               a.slug, a.scopes AS agent_scopes, a.allowed_collections AS agent_collections,
               a.status AS agent_status
        FROM oauth_tokens t
        JOIN oauth_grants g ON g.id = t.grant_id
        JOIN dash_users u ON u.id = g.user_id
        LEFT JOIN agents a ON a.id = g.agent_id
        WHERE t.token_hash = $1 AND t.kind = 'access'
        """,
        hash_key(raw),
    )
    invalid = PermissionDenied("Token OAuth inválido, expirado ou revogado.")
    if row is None or row["revoked_at"] is not None or row["grant_revoked_at"] is not None:
        raise invalid
    if row["disabled_at"] is not None or row["expires_at"] <= datetime.now(UTC):
        raise invalid
    cap = OAUTH_ROLE_SCOPES.get(row["role"], ())
    if not set(row["scopes"]) <= set(cap):
        raise invalid
    await conn.execute(
        "UPDATE oauth_grants SET last_used_at = now() WHERE id = $1 "
        "AND (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')",
        row["grant_id"],
    )
    if row["agent_id"] is None:
        # mesmo actor da dash: o que a pessoa faz pelo Claude aparece como dela (contribuidores, histórico)
        return Principal(
            actor=f"dash:{row['username']}",
            scopes=frozenset(row["scopes"]),
            allowed_collections=tuple(row["allowed_collections"]),
        )
    if row["agent_status"] != "active":
        raise PermissionDenied("O agente desta conexão está arquivado.")
    return Principal(
        actor=f"agent:{row['slug']}",
        scopes=frozenset(row["agent_scopes"]),
        agent_id=row["agent_id"],
        agent_slug=row["slug"],
        allowed_collections=tuple(row["agent_collections"]),
    )


async def resolve_bearer(conn: asyncpg.Connection, raw: str) -> Principal:
    """Bearer do /mcp e do REST: chave de API (kb_sk_) ou access token OAuth (kb_at_)."""
    if raw.startswith(OAUTH_ACCESS_PREFIX):
        return await resolve_oauth_token(conn, raw)
    return await resolve_key(conn, raw)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None
