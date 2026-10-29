"""OAuth 2.1 do MCP (plan-web-17): o Claude (ou outro cliente MCP) conecta pela tela "Autorizar".

O SDK do MCP cuida do protocolo (metadata, /authorize, /token, /register, /revoke, PKCE); aqui fica
o provider que persiste tudo no Postgres e a lógica da tela de consentimento e de Conexões.

Fluxo: /register (DCR) → /authorize grava um pedido e manda para /oauth/consent → a pessoa (logada
na dash) aprova → nasce uma conexão (oauth_grants) + código → /token troca por access (kb_at_) e
refresh (kb_rt_, rotativo). Códigos e tokens só em hash.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import asyncpg
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ..config import get_settings, oauth_redirect_hosts, oauth_redirect_schemes
from ..db import pool
from ..logging_config import get_logger
from ..security import (
    OAUTH_ACCESS_PREFIX,
    OAUTH_REFRESH_PREFIX,
    OAUTH_ROLE_SCOPES,
    KBError,
    NotFound,
    PermissionDenied,
    hash_key,
)
from .dash_auth import DashUser

logger = get_logger()

# escopos que o Concilium entende via OAuth (o "admin" fica de fora: chaves/usuários só na dash)
OAUTH_SCOPES = ("read", "write", "agents:manage")
CODE_TTL = timedelta(minutes=5)
REQUEST_TTL = timedelta(minutes=10)
CONSENT_PATH = "/oauth/consent"


def _now() -> datetime:
    return datetime.now(UTC)


def _new_secret(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)  # 256 bits


def resource_url() -> str:
    """Identificador do recurso protegido (RFC 8707/9728): o endpoint MCP."""
    return get_settings().public_url.rstrip("/") + "/mcp"


def _valid_resource(resource: str | None) -> bool:
    if resource is None:
        return True
    base = get_settings().public_url.rstrip("/")
    return resource.rstrip("/") in (base, base + "/mcp")


def redirect_allowed(uri: str) -> bool:
    """https em host da allowlist (ou subdomínio dele), loopback http (Claude Code, Inspector) ou
    esquema de app nativo da allowlist (Cursor, VS Code)."""
    url = urlparse(uri)
    scheme, host = url.scheme.lower(), (url.hostname or "").lower()
    if scheme == "http":
        return host in ("localhost", "127.0.0.1", "::1")
    if scheme == "https":
        return bool(host) and any(host == h or host.endswith("." + h) for h in oauth_redirect_hosts(get_settings()))
    return scheme in oauth_redirect_schemes(get_settings())


def redirect_label(uri: str) -> str | None:
    """Para onde o app volta, como a tela mostra: o host (web) ou esquema://app (nativo)."""
    url = urlparse(uri)
    if url.scheme in ("http", "https"):
        return url.hostname
    return f"{url.scheme}://{url.netloc}" if url.scheme else None


class ConciliumAuthCode(AuthorizationCode):
    grant_id: str


class ConciliumRefreshToken(RefreshToken):
    grant_id: str


class ConciliumAccessToken(AccessToken):
    grant_id: str


async def _issue_tokens(conn: asyncpg.Connection, grant_id: uuid.UUID, scopes: list[str]) -> OAuthToken:
    s = get_settings()
    access, refresh = _new_secret(OAUTH_ACCESS_PREFIX), _new_secret(OAUTH_REFRESH_PREFIX)
    access_ttl = timedelta(minutes=s.oauth_access_ttl_minutes)
    await conn.executemany(
        "INSERT INTO oauth_tokens (token_hash, grant_id, kind, scopes, expires_at) VALUES ($1, $2, $3, $4, $5)",
        [
            (hash_key(access), grant_id, "access", scopes, _now() + access_ttl),
            (hash_key(refresh), grant_id, "refresh", scopes, _now() + timedelta(days=s.oauth_refresh_ttl_days)),
        ],
    )
    return OAuthToken(
        access_token=access,
        token_type="Bearer",
        expires_in=int(access_ttl.total_seconds()),
        scope=" ".join(scopes),
        refresh_token=refresh,
    )


async def _revoke_grant(conn: asyncpg.Connection, grant_id: uuid.UUID) -> None:
    await conn.execute("UPDATE oauth_grants SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL", grant_id)
    await conn.execute(
        "UPDATE oauth_tokens SET revoked_at = now() WHERE grant_id = $1 AND revoked_at IS NULL", grant_id
    )


class ConciliumOAuthProvider(
    OAuthAuthorizationServerProvider[ConciliumAuthCode, ConciliumRefreshToken, ConciliumAccessToken]
):
    """Implementa o OAuthAuthorizationServerProvider do SDK sobre o Postgres (o grant ID-JAG fica
    com a implementação padrão do SDK, que recusa)."""

    # ------------------------------------------------------------ clientes (DCR)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = await pool().fetchval("SELECT metadata FROM oauth_clients WHERE client_id = $1", client_id)
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        if not uris:
            raise RegistrationError("invalid_redirect_uri", "Informe ao menos um redirect_uri.")
        refused = [u for u in uris if not redirect_allowed(u)]
        if refused:
            logger.warning("oauth: registro recusado (redirect fora da allowlist): %s", refused)
            raise RegistrationError(
                "invalid_redirect_uri",
                f"redirect_uri não permitido neste servidor: {', '.join(refused)} "
                "(aceitos: https nos hosts de OAUTH_REDIRECT_ALLOWLIST, esquemas de OAUTH_REDIRECT_SCHEMES "
                "ou http://localhost).",
            )
        # o cliente pode pedir no /authorize qualquer escopo do Concilium (o SDK valida contra este campo);
        # o que vale é o que a pessoa marca na tela
        requested = set((client_info.scope or "").split())
        client_info.scope = " ".join(sorted(requested | set(OAUTH_SCOPES)))
        await pool().execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, metadata) VALUES ($1, $2, $3, $4)",
            client_info.client_id,
            client_info.client_name,
            uris,
            client_info.model_dump_json(),
        )
        logger.info("oauth: cliente registrado (name=%s, redirect=%s)", client_info.client_name, uris)

    # ------------------------------------------------------------ /authorize → tela de consentimento

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not _valid_resource(params.resource):
            raise AuthorizeError("invalid_target", f"Recurso desconhecido: {params.resource}")
        request_id = _new_secret()
        await pool().execute(
            """
            INSERT INTO oauth_auth_requests
              (id, client_id, redirect_uri, redirect_uri_provided, code_challenge, state, scopes, resource, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            request_id,
            client.client_id,
            str(params.redirect_uri),
            params.redirect_uri_provided_explicitly,
            params.code_challenge,
            params.state,
            params.scopes or [],
            params.resource,
            _now() + REQUEST_TTL,
        )
        return f"{get_settings().public_url.rstrip('/')}{CONSENT_PATH}?request={request_id}"

    # ------------------------------------------------------------ código → tokens

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> ConciliumAuthCode | None:
        row = await pool().fetchrow(
            """
            SELECT c.redirect_uri, c.redirect_uri_provided, c.code_challenge, c.expires_at, c.used_at,
                   g.id AS grant_id, g.client_id, g.scopes, g.resource, g.revoked_at, u.username
            FROM oauth_codes c JOIN oauth_grants g ON g.id = c.grant_id JOIN dash_users u ON u.id = g.user_id
            WHERE c.code_hash = $1
            """,
            hash_key(authorization_code),
        )
        if row is None or row["client_id"] != client.client_id or row["revoked_at"] is not None:
            return None
        if row["used_at"] is not None:
            # código reapresentado: alguém pode ter interceptado — derruba a conexão inteira (RFC 6749 §4.1.2)
            async with pool().acquire() as conn:
                await _revoke_grant(conn, row["grant_id"])
            logger.warning("oauth: código reutilizado — conexão %s revogada", row["grant_id"])
            return None
        return ConciliumAuthCode(
            code=authorization_code,
            scopes=list(row["scopes"]),
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=row["redirect_uri_provided"],
            resource=row["resource"],
            subject=row["username"],
            grant_id=str(row["grant_id"]),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: ConciliumAuthCode
    ) -> OAuthToken:
        async with pool().acquire() as conn, conn.transaction():
            used = await conn.fetchval(
                "UPDATE oauth_codes SET used_at = now() WHERE code_hash = $1 AND used_at IS NULL RETURNING 1",
                hash_key(authorization_code.code),
            )
            if not used:
                raise TokenError("invalid_grant", "Código de autorização já usado.")
            return await _issue_tokens(conn, uuid.UUID(authorization_code.grant_id), authorization_code.scopes)

    # ------------------------------------------------------------ refresh (rotativo)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> ConciliumRefreshToken | None:
        row = await pool().fetchrow(
            """
            SELECT t.scopes, t.expires_at, t.revoked_at, g.id AS grant_id, g.client_id, g.resource,
                   g.revoked_at AS grant_revoked_at
            FROM oauth_tokens t JOIN oauth_grants g ON g.id = t.grant_id
            WHERE t.token_hash = $1 AND t.kind = 'refresh'
            """,
            hash_key(refresh_token),
        )
        if row is None or row["client_id"] != client.client_id or row["grant_revoked_at"] is not None:
            return None
        if row["revoked_at"] is not None:
            # refresh já trocado sendo reapresentado = vazou: revoga a conexão (OAuth 2.1 §4.3.1)
            async with pool().acquire() as conn:
                await _revoke_grant(conn, row["grant_id"])
            logger.warning("oauth: refresh token reutilizado — conexão %s revogada", row["grant_id"])
            return None
        return ConciliumRefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=int(row["expires_at"].timestamp()),
            resource=row["resource"],
            grant_id=str(row["grant_id"]),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: ConciliumRefreshToken, scopes: list[str]
    ) -> OAuthToken:
        grant_id = uuid.UUID(refresh_token.grant_id)
        async with pool().acquire() as conn, conn.transaction():
            rotated = await conn.fetchval(
                "UPDATE oauth_tokens SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL RETURNING 1",
                hash_key(refresh_token.token),
            )
            if not rotated:
                raise TokenError("invalid_grant", "Refresh token já usado.")
            # o access anterior sai junto: só o par recém-emitido vale
            await conn.execute(
                "UPDATE oauth_tokens SET revoked_at = now() "
                "WHERE grant_id = $1 AND kind = 'access' AND revoked_at IS NULL",
                grant_id,
            )
            return await _issue_tokens(conn, grant_id, scopes)

    # ------------------------------------------------------------ access token / revogação

    async def load_access_token(self, token: str) -> ConciliumAccessToken | None:
        row = await pool().fetchrow(
            """
            SELECT t.scopes, t.expires_at, g.id AS grant_id, g.client_id, g.resource
            FROM oauth_tokens t JOIN oauth_grants g ON g.id = t.grant_id
            WHERE t.token_hash = $1 AND t.kind = 'access' AND t.revoked_at IS NULL AND g.revoked_at IS NULL
              AND t.expires_at > now()
            """,
            hash_key(token),
        )
        if row is None:
            return None
        return ConciliumAccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=int(row["expires_at"].timestamp()),
            resource=row["resource"],
            grant_id=str(row["grant_id"]),
        )

    async def revoke_token(self, token: ConciliumAccessToken | ConciliumRefreshToken) -> None:
        # revogar qualquer um dos dois derruba a conexão inteira (o SDK recomenda os dois)
        async with pool().acquire() as conn:
            await _revoke_grant(conn, uuid.UUID(token.grant_id))


provider = ConciliumOAuthProvider()


# ---------------------------------------------------------------- tela de consentimento


async def _pending_request(request_id: str) -> asyncpg.Record:
    row = await pool().fetchrow(
        """
        SELECT r.*, c.client_name, c.metadata->>'client_uri' AS client_uri
        FROM oauth_auth_requests r JOIN oauth_clients c ON c.client_id = r.client_id
        WHERE r.id = $1
        """,
        request_id,
    )
    if row is None or row["expires_at"] <= _now():
        raise NotFound("Pedido de autorização expirado ou inexistente: volte ao aplicativo e conecte de novo.")
    return row


def role_scopes(user: DashUser) -> list[str]:
    return list(OAUTH_ROLE_SCOPES.get(user.role, ()))


async def consent_info(request_id: str, user: DashUser | None) -> dict[str, Any]:
    """O que a tela mostra: app, para onde volta, quem está logado e o que pode ser concedido."""
    row = await _pending_request(request_id)
    info: dict[str, Any] = {
        "client_name": row["client_name"] or "Aplicativo MCP",
        "redirect_host": redirect_label(row["redirect_uri"]),
        "requested_scopes": [s for s in row["scopes"] if s in OAUTH_SCOPES],
        "user": None,
    }
    if user is None:
        return info
    collections = await pool().fetch("SELECT name, description FROM collections ORDER BY name")
    info["user"] = {"username": user.username, "role": user.role}
    info["available_scopes"] = role_scopes(user)
    info["collections"] = [dict(r) for r in collections]
    if user.is_admin:
        agents = await pool().fetch("SELECT slug, name FROM agents WHERE status = 'active' ORDER BY name")
        info["agents"] = [dict(r) for r in agents]
    else:
        info["agents"] = []
    return info


async def decide(
    request_id: str,
    user: DashUser,
    *,
    approve: bool,
    scopes: list[str],
    collections: list[str],
    agent_slug: str | None,
) -> str:
    """Aprova ou recusa o pedido; devolve a URL de volta para o cliente (com code ou error)."""
    row = await _pending_request(request_id)
    async with pool().acquire() as conn, conn.transaction():
        # pedido de uso único: some antes de qualquer outra coisa
        deleted = await conn.fetchval("DELETE FROM oauth_auth_requests WHERE id = $1 RETURNING 1", request_id)
        if not deleted:
            raise NotFound("Pedido de autorização já respondido.")
        if not approve:
            logger.info("oauth: autorização recusada (username=%s client=%s)", user.username, row["client_name"])
            return construct_redirect_uri(
                row["redirect_uri"], error="access_denied", error_description="Acesso recusado.", state=row["state"]
            )

        allowed = role_scopes(user)
        granted = sorted(set(scopes))
        if "read" not in granted:
            raise KBError("A permissão de consultar a base é obrigatória.")
        if not set(granted) <= set(allowed):
            raise PermissionDenied("Seu papel não permite conceder estas permissões.")
        cols = sorted(set(collections))
        if cols:
            known = {r["name"] for r in await conn.fetch("SELECT name FROM collections WHERE name = ANY($1)", cols)}
            if missing := [c for c in cols if c not in known]:
                raise NotFound(f"Pastas não encontradas: {', '.join(missing)}")
        agent_id = None
        if agent_slug:
            if not user.is_admin:
                raise PermissionDenied("Só administradores conectam usando o perfil de um agente.")
            agent_id = await conn.fetchval("SELECT id FROM agents WHERE slug = $1 AND status = 'active'", agent_slug)
            if agent_id is None:
                raise NotFound(f"Agente '{agent_slug}' não encontrado.")
            cols = []  # o perfil do agente define escopos e pastas

        # reconectar o mesmo app substitui a conexão anterior (Conexões sem duplicata)
        old = await conn.fetch(
            "SELECT id FROM oauth_grants WHERE client_id = $1 AND user_id = $2 AND revoked_at IS NULL",
            row["client_id"],
            uuid.UUID(user.id),
        )
        for g in old:
            await _revoke_grant(conn, g["id"])
        grant_id = await conn.fetchval(
            """
            INSERT INTO oauth_grants (client_id, user_id, agent_id, scopes, allowed_collections, resource)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
            """,
            row["client_id"],
            uuid.UUID(user.id),
            agent_id,
            granted,
            cols,
            row["resource"],
        )
        code = _new_secret()
        await conn.execute(
            """
            INSERT INTO oauth_codes
              (code_hash, grant_id, redirect_uri, redirect_uri_provided, code_challenge, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            hash_key(code),
            grant_id,
            row["redirect_uri"],
            row["redirect_uri_provided"],
            row["code_challenge"],
            _now() + CODE_TTL,
        )
    logger.info(
        "oauth: autorizado (username=%s client=%s scopes=%s agent=%s)",
        user.username,
        row["client_name"],
        granted,
        agent_slug,
    )
    return construct_redirect_uri(row["redirect_uri"], code=code, state=row["state"])


# ---------------------------------------------------------------- Conexões (dash)


async def list_grants(user: DashUser, *, all_users: bool = False) -> list[dict[str, Any]]:
    if all_users and not user.is_admin:
        raise PermissionDenied("Só administradores veem as conexões de todos.")
    rows = await pool().fetch(
        """
        SELECT g.id, g.scopes, g.allowed_collections, g.created_at, g.last_used_at,
               c.client_name, c.redirect_uris, u.username, a.slug AS agent_slug, a.name AS agent_name
        FROM oauth_grants g
        JOIN oauth_clients c ON c.client_id = g.client_id
        JOIN dash_users u ON u.id = g.user_id
        LEFT JOIN agents a ON a.id = g.agent_id
        WHERE g.revoked_at IS NULL AND ($1 OR g.user_id = $2)
        ORDER BY coalesce(g.last_used_at, g.created_at) DESC
        """,
        all_users,
        uuid.UUID(user.id),
    )
    return [
        {
            "id": str(r["id"]),
            "client_name": r["client_name"] or "Aplicativo MCP",
            "redirect_host": redirect_label(r["redirect_uris"][0]) if r["redirect_uris"] else None,
            "scopes": list(r["scopes"]),
            "collections": list(r["allowed_collections"]),
            "username": r["username"],
            "agent_slug": r["agent_slug"],
            "agent_name": r["agent_name"],
            "created_at": r["created_at"].isoformat(),
            "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
        }
        for r in rows
    ]


async def revoke_grant(grant_id: str, user: DashUser) -> None:
    try:
        gid = uuid.UUID(grant_id)
    except ValueError as e:
        raise NotFound(f"Conexão {grant_id} não encontrada.") from e
    owner = await pool().fetchval("SELECT user_id FROM oauth_grants WHERE id = $1 AND revoked_at IS NULL", gid)
    if owner is None:
        raise NotFound(f"Conexão {grant_id} não encontrada.")
    if str(owner) != user.id and not user.is_admin:
        raise PermissionDenied("Você só pode revogar as suas conexões.")
    async with pool().acquire() as conn:
        await _revoke_grant(conn, gid)
    logger.info("oauth: conexão %s revogada por %s", grant_id, user.username)


async def revoke_user_grants(user_id: str) -> None:
    """Desativar, rebaixar ou trocar a senha de um usuário derruba as conexões OAuth dele."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM oauth_grants WHERE user_id = $1 AND revoked_at IS NULL", uuid.UUID(user_id)
        )
        for r in rows:
            await _revoke_grant(conn, r["id"])


async def cleanup() -> dict[str, int]:
    """Higiene (junto do `mcp-rag-api cleanup`): pedidos/códigos/tokens vencidos, conexões revogadas
    e clientes registrados que nunca viraram conexão em 30 dias."""
    p = pool()
    counts = {
        "oauth_requests_deleted": await p.fetchval(
            "WITH d AS (DELETE FROM oauth_auth_requests WHERE expires_at <= now() RETURNING 1) SELECT count(*) FROM d"
        ),
        "oauth_codes_deleted": await p.fetchval(
            "WITH d AS (DELETE FROM oauth_codes WHERE expires_at <= now() - interval '1 day' RETURNING 1) "
            "SELECT count(*) FROM d"
        ),
        "oauth_tokens_deleted": await p.fetchval(
            # revogado fica até vencer: é ele que detecta o reuso de um refresh token roubado (e derruba a
            # conexão inteira); apagado antes, o replay só daria "não encontrado"
            "WITH d AS (DELETE FROM oauth_tokens WHERE expires_at <= now() RETURNING 1) SELECT count(*) FROM d"
        ),
        "oauth_grants_deleted": await p.fetchval(
            "WITH d AS (DELETE FROM oauth_grants WHERE revoked_at IS NOT NULL RETURNING 1) SELECT count(*) FROM d"
        ),
        "oauth_clients_deleted": await p.fetchval(
            """
            WITH d AS (
              DELETE FROM oauth_clients c
              WHERE c.created_at < now() - interval '30 days'
                AND NOT EXISTS (SELECT 1 FROM oauth_grants g WHERE g.client_id = c.client_id)
              RETURNING 1
            ) SELECT count(*) FROM d
            """
        ),
    }
    return counts
