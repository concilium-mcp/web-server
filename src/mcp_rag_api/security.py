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
from uuid import UUID

import asyncpg

VALID_SCOPES = {"read", "write", "agents:manage", "admin"}
_IMPLIES = {"write": {"read"}, "agents:manage": {"read"}}
KEY_PREFIX = "kb_sk_"


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
    await conn.execute("UPDATE api_keys SET last_used_at = now() WHERE id = $1", row["id"])
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


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None
