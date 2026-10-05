"""Autenticação da dashboard: usuários locais (PBKDF2 do stdlib) + sessões opacas.

Separada do Bearer da API pública: aqui quem entra é um humano, com senha e sessão
em cookie HttpOnly. Só o hash do token vai para o banco (igual ao padrão das api_keys).
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from ..db import pool
from ..security import KBError

PBKDF2_ITERATIONS = 210_000  # recomendação OWASP para PBKDF2-HMAC-SHA256
SESSION_TTL = timedelta(days=7)
SESSION_COOKIE = "dash_session"
ROLES = ("admin", "editor", "viewer")


@dataclass(frozen=True)
class DashUser:
    id: str
    username: str
    role: str  # "admin" | "editor" | "viewer"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_edit(self) -> bool:
        """Cria/edita/arquiva notas: editor e admin."""
        return self.role in ("admin", "editor")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_user(username: str, password: str, role: str = "viewer") -> dict:
    username = username.strip().lower()
    if not username:
        raise KBError("Username é obrigatório.")
    if len(password) < 8:
        raise KBError("A senha deve ter ao menos 8 caracteres.")
    if role not in ROLES:
        raise KBError("Role inválida: use 'admin', 'editor' ou 'viewer'.")
    try:
        user_id = await pool().fetchval(
            "INSERT INTO dash_users (username, password_hash, role) VALUES ($1, $2, $3) RETURNING id",
            username,
            hash_password(password),
            role,
        )
    except asyncpg.UniqueViolationError as e:
        raise KBError(f"Já existe um usuário '{username}'.") from e
    return {"id": str(user_id), "username": username, "role": role}


async def authenticate(username: str, password: str) -> DashUser | None:
    row = await pool().fetchrow(
        "SELECT id, username, role, password_hash, disabled_at FROM dash_users WHERE username = $1",
        username.strip().lower(),
    )
    if row is None or row["disabled_at"] is not None:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return DashUser(id=str(row["id"]), username=row["username"], role=row["role"])


async def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    await pool().execute(
        "INSERT INTO dash_sessions (user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
        uuid.UUID(user_id),
        _hash_token(token),
        datetime.now(UTC) + SESSION_TTL,
    )
    return token


async def resolve_session(token: str) -> DashUser | None:
    row = await pool().fetchrow(
        """
        SELECT u.id, u.username, u.role, u.disabled_at, s.expires_at, s.revoked_at
        FROM dash_sessions s
        JOIN dash_users u ON u.id = s.user_id
        WHERE s.token_hash = $1
        """,
        _hash_token(token),
    )
    if row is None:
        return None
    if row["revoked_at"] is not None or row["disabled_at"] is not None:
        return None
    if row["expires_at"] < datetime.now(UTC):
        return None
    return DashUser(id=str(row["id"]), username=row["username"], role=row["role"])


async def revoke_session(token: str) -> None:
    await pool().execute(
        "UPDATE dash_sessions SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL",
        _hash_token(token),
    )


async def revoke_user_sessions(user_id: str) -> None:
    """Usado ao desativar/resetar um usuário: derruba todas as sessões dele."""
    await pool().execute(
        "UPDATE dash_sessions SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
        uuid.UUID(user_id),
    )
