"""Autenticação da dashboard: usuários locais (PBKDF2 do stdlib) + sessões opacas.

Separada do Bearer da API pública: aqui quem entra é um humano, com senha e sessão
em cookie HttpOnly. Só o hash do token vai para o banco (igual ao padrão das api_keys).
"""

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from ..config import get_settings
from ..db import pool
from ..security import KBError

PBKDF2_ITERATIONS = 210_000  # recomendação OWASP para PBKDF2-HMAC-SHA256
SESSION_COOKIE = "dash_session"
ROLES = ("admin", "editor", "viewer")


def session_ttl() -> timedelta:
    """Duração de uma sessão da dash: DASH_SESSION_TTL_HOURS (padrão 7 dias)."""
    return timedelta(hours=get_settings().dash_session_ttl_hours)


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
        datetime.now(UTC) + session_ttl(),
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
    """Usado ao desativar/resetar/rebaixar um usuário: derruba todas as sessões dele."""
    await pool().execute(
        "UPDATE dash_sessions SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
        uuid.UUID(user_id),
    )


# ---------------------------------------------------------------- rate limit do login

# Backoff exponencial: a 1ª falha acima do limite bloqueia ~2 s e dobra a cada nova
# tentativa, até o teto de 15 min. Contagem em memória (um processo só); uma falha
# contra um usuário inexistente também conta — senão o limite seria trivial de furar.
LOGIN_BLOCK_BASE_SECONDS = 2.0
LOGIN_BLOCK_MAX_SECONDS = 900.0


class LoginRateLimiter:
    """Contador de falhas de login por chave (username, IP), com bloqueio crescente.

    Não persiste nada: o objetivo é apenas frear força bruta online. Desligado
    quando max_failures == 0 (DASH_LOGIN_RATE_LIMIT=0).
    """

    def __init__(self, max_failures: int, base_seconds: float = LOGIN_BLOCK_BASE_SECONDS) -> None:
        self.max_failures = max_failures
        self.base_seconds = base_seconds
        self._failures: dict[tuple[str, str], tuple[int, float]] = {}

    def blocked_for(self, key: tuple[str, str]) -> float:
        """Segundos restantes de bloqueio para a chave (0 = pode tentar)."""
        if self.max_failures <= 0:
            return 0.0
        entry = self._failures.get(key)
        if not entry:
            return 0.0
        _, blocked_until = entry
        return max(0.0, blocked_until - time.monotonic())

    def register_failure(self, key: tuple[str, str]) -> None:
        if self.max_failures <= 0:
            return
        failures, _ = self._failures.get(key, (0, 0.0))
        failures += 1
        blocked_until = 0.0
        # atingiu o limite: bloqueia a próxima tentativa; cada nova falha dobra a espera
        if failures >= self.max_failures:
            delay = min(self.base_seconds * 2 ** (failures - self.max_failures), LOGIN_BLOCK_MAX_SECONDS)
            blocked_until = time.monotonic() + delay
        self._failures[key] = (failures, blocked_until)
        if len(self._failures) > 10_000:  # sanity: não deixa o dict crescer para sempre
            now = time.monotonic()
            self._failures = {k: v for k, v in self._failures.items() if v[1] > now - LOGIN_BLOCK_MAX_SECONDS}

    def reset(self, key: tuple[str, str]) -> None:
        self._failures.pop(key, None)


_login_limiter: LoginRateLimiter | None = None


def login_rate_limiter() -> LoginRateLimiter:
    """Instância única do limiter, reconstruída se o env mudar (ex.: testes)."""
    global _login_limiter
    limit = get_settings().dash_login_rate_limit
    if _login_limiter is None or _login_limiter.max_failures != limit:
        _login_limiter = LoginRateLimiter(limit)
    return _login_limiter


async def cleanup_sessions() -> dict:
    """Higiene periódica (rodar via `mcp-rag-api cleanup`): apaga sessões da dash
    expiradas ou revogadas — sem isso a tabela só cresce."""
    expired = await pool().fetchval(
        "WITH d AS (DELETE FROM dash_sessions WHERE expires_at <= now() RETURNING 1) SELECT count(*) FROM d"
    )
    revoked = await pool().fetchval(
        "WITH d AS (DELETE FROM dash_sessions WHERE revoked_at IS NOT NULL RETURNING 1) SELECT count(*) FROM d"
    )
    return {"dash_sessions_expired_deleted": expired, "dash_sessions_revoked_deleted": revoked}
