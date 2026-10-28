"""Autenticação em dois fatores da dashboard: TOTP (RFC 6238) + recovery codes.

TOTP com o stdlib (HMAC-SHA1, 6 dígitos, passo de 30 s — o padrão que Google Authenticator,
Authy, 1Password e afins entendem). Aceita ±1 passo de relógio e nunca o mesmo passo duas
vezes (totp_last_step). O QR code do cadastro sai em SVG pelo segno.

Fluxo de login com 2FA: senha certa → desafio (token opaco, 5 min, poucas tentativas) →
código do app ou recovery code → sessão. Só hashes de token e de recovery code vão para o banco.

O segredo TOTP precisa ser lido de volta para conferir códigos, então não dá para guardar só o hash.
Com DASH_TOTP_ENCRYPTION_KEY definida ele vai cifrado (AES-256-GCM, "enc:v1:..."); sem ela, em
texto puro — opcional porque é self-host e mais um segredo para guardar nem sempre compensa.
Segredos antigos em texto puro continuam funcionando; `mcp-rag-api encrypt-2fa-secrets` cifra todos.
"""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

import segno
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings
from ..db import pool
from ..logging_config import get_logger
from ..security import KBError

logger = get_logger()

TOTP_DIGITS = 6
TOTP_PERIOD = 30
TOTP_WINDOW = 1  # passos aceitos para cada lado (relógio do celular adiantado/atrasado)
TOTP_ISSUER = "Concilium"
RECOVERY_CODES = 10
CHALLENGE_TTL = timedelta(minutes=5)
CHALLENGE_MAX_ATTEMPTS = 5
ENC_PREFIX = "enc:v1:"
UNREADABLE = "O 2FA desta conta não pode ser verificado agora: fale com o administrador do servidor."


# ---------------------------------------------------------------- TOTP puro


def new_secret() -> str:
    """Segredo de 160 bits em base32 sem padding (o formato que os apps esperam)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _b32decode(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp_at(secret: str, step: int) -> str:
    digest = hmac.new(_b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // TOTP_PERIOD)


def match_step(secret: str, code: str, now: float | None = None) -> int | None:
    """Passo em que o código confere (dentro da janela), ou None."""
    code = normalize_code(code)
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    base = current_step(now)
    for step in range(base - TOTP_WINDOW, base + TOTP_WINDOW + 1):
        if hmac.compare_digest(totp_at(secret, step), code):
            return step
    return None


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code if not ch.isspace() and ch != "-").lower()


def otpauth_uri(secret: str, username: str) -> str:
    label = quote(f"{TOTP_ISSUER}:{username}")
    params = urlencode({"secret": secret, "issuer": TOTP_ISSUER, "digits": TOTP_DIGITS, "period": TOTP_PERIOD})
    return f"otpauth://totp/{label}?{params}"


def qr_svg(data: str) -> str:
    """QR code em SVG (string), para a tela mostrar via data: URI (CSP img-src permite data:)."""
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, dark="#000", light="#fff")
    return buf.getvalue().decode()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_recovery_codes() -> tuple[list[str], list[str]]:
    """(códigos para mostrar uma única vez, hashes para guardar). Formato xxxxx-xxxxx."""
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(5)
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, [_hash(normalize_code(c)) for c in codes]


# ---------------------------------------------------------------- cifragem opcional do segredo


def _cipher() -> AESGCM | None:
    key = get_settings().dash_totp_encryption_key
    if not key:
        return None
    # a variável é texto livre (ex.: `openssl rand -base64 32`): deriva 256 bits com rótulo próprio
    return AESGCM(hashlib.sha256(b"concilium-totp-v1\0" + key.encode()).digest())


def seal(secret: str, user_id: str) -> str:
    """Segredo como vai para o banco: cifrado se houver chave, senão como veio.
    O user_id entra como dado autenticado: copiar o valor para outra conta não decifra."""
    cipher = _cipher()
    if cipher is None:
        return secret
    nonce = secrets.token_bytes(12)
    sealed = cipher.encrypt(nonce, secret.encode(), str(user_id).encode())
    return ENC_PREFIX + base64.b64encode(nonce + sealed).decode()


def unseal(stored: str, user_id: str) -> str:
    if not stored.startswith(ENC_PREFIX):
        return stored  # texto puro (sem chave, ou gravado antes de ligar a cifragem)
    cipher = _cipher()
    if cipher is None:
        logger.error("2fa: segredo cifrado no banco, mas DASH_TOTP_ENCRYPTION_KEY não está definida")
        raise KBError(UNREADABLE)
    raw = base64.b64decode(stored[len(ENC_PREFIX) :])
    try:
        return cipher.decrypt(raw[:12], raw[12:], str(user_id).encode()).decode()
    except InvalidTag:
        logger.error("2fa: não consegui decifrar o segredo (user_id=%s); DASH_TOTP_ENCRYPTION_KEY mudou?", user_id)
        raise KBError(UNREADABLE) from None


async def reseal_all(*, decrypt: bool = False) -> int:
    """Cifra (ou, com decrypt=True, volta a texto puro) os segredos de todas as contas.
    Para trocar de chave: decrypt com a chave antiga, troca a variável, cifra de novo."""
    if not decrypt and _cipher() is None:
        raise KBError("Defina DASH_TOTP_ENCRYPTION_KEY antes de cifrar os segredos do 2FA.")
    changed = 0
    async with pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT id, totp_secret, totp_pending_secret FROM dash_users "
            "WHERE totp_secret IS NOT NULL OR totp_pending_secret IS NOT NULL FOR UPDATE"
        )
        for row in rows:
            uid = str(row["id"])
            new: list[str | None] = []
            for value in (row["totp_secret"], row["totp_pending_secret"]):
                # vazio, ou já cifrado e o pedido é cifrar: fica como está
                if value is None or (not decrypt and value.startswith(ENC_PREFIX)):
                    new.append(value)
                    continue
                plain = unseal(value, uid)
                new.append(plain if decrypt else seal(plain, uid))
            if new != [row["totp_secret"], row["totp_pending_secret"]]:
                await conn.execute(
                    "UPDATE dash_users SET totp_secret = $2, totp_pending_secret = $3 WHERE id = $1",
                    row["id"],
                    *new,
                )
                changed += 1
    return changed


# ---------------------------------------------------------------- estado no banco


async def status(user_id: str) -> dict:
    row = await pool().fetchrow(
        "SELECT totp_enabled_at, cardinality(totp_recovery_hashes) AS left FROM dash_users WHERE id = $1",
        uuid.UUID(user_id),
    )
    enabled = row is not None and row["totp_enabled_at"] is not None
    return {
        "enabled": enabled,
        "enabled_at": row["totp_enabled_at"].isoformat() if enabled else None,
        "recovery_codes_left": row["left"] if enabled else 0,
    }


async def is_enabled(user_id: str) -> bool:
    return bool(
        await pool().fetchval("SELECT totp_enabled_at IS NOT NULL FROM dash_users WHERE id = $1", uuid.UUID(user_id))
    )


async def begin_setup(user_id: str, username: str) -> dict:
    """Gera um segredo pendente (substitui um pendente anterior). O 2FA só liga no confirm_setup."""
    secret = new_secret()
    await pool().execute(
        "UPDATE dash_users SET totp_pending_secret = $2, updated_at = now() WHERE id = $1",
        uuid.UUID(user_id),
        seal(secret, user_id),
    )
    uri = otpauth_uri(secret, username)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr_svg(uri)}


async def confirm_setup(user_id: str, code: str) -> list[str]:
    """Confere o 1º código contra o segredo pendente e liga o 2FA. Devolve os recovery codes."""
    uid = uuid.UUID(user_id)
    pending = await pool().fetchval("SELECT totp_pending_secret FROM dash_users WHERE id = $1", uid)
    if not pending:
        raise KBError("Nenhuma configuração de 2FA em andamento: gere o QR code de novo.")
    step = match_step(unseal(pending, user_id), code)
    if step is None:
        raise KBError("Código inválido. Confira o horário do celular e tente o código atual.")
    codes, hashes = new_recovery_codes()
    await pool().execute(
        """
        UPDATE dash_users SET totp_secret = totp_pending_secret, totp_pending_secret = NULL,
               totp_enabled_at = now(), totp_last_step = $2, totp_recovery_hashes = $3, updated_at = now()
        WHERE id = $1
        """,
        uid,
        step,
        hashes,
    )
    return codes


async def verify(user_id: str, code: str) -> str | None:
    """Confere um código do app (sem reuso do mesmo passo) ou consome um recovery code.
    Devolve "totp" / "recovery" quando aceito, None quando não."""
    uid = uuid.UUID(user_id)
    secret = await pool().fetchval(
        "SELECT totp_secret FROM dash_users WHERE id = $1 AND totp_enabled_at IS NOT NULL", uid
    )
    if not secret:
        return None
    step = match_step(unseal(secret, user_id), code)
    if step is not None:
        # condicional no próprio UPDATE: duas requests com o mesmo código não passam as duas
        accepted = await pool().fetchval(
            "UPDATE dash_users SET totp_last_step = $2 WHERE id = $1 "
            "AND (totp_last_step IS NULL OR totp_last_step < $2) RETURNING 1",
            uid,
            step,
        )
        return "totp" if accepted else None
    digest = _hash(normalize_code(code))
    consumed = await pool().fetchval(
        "UPDATE dash_users SET totp_recovery_hashes = array_remove(totp_recovery_hashes, $2) "
        "WHERE id = $1 AND $2 = ANY(totp_recovery_hashes) RETURNING 1",
        uid,
        digest,
    )
    return "recovery" if consumed else None


async def regenerate_recovery_codes(user_id: str) -> list[str]:
    codes, hashes = new_recovery_codes()
    await pool().execute(
        "UPDATE dash_users SET totp_recovery_hashes = $2, updated_at = now() WHERE id = $1",
        uuid.UUID(user_id),
        hashes,
    )
    return codes


async def disable(user_id: str) -> None:
    """Desliga o 2FA (pela própria pessoa, já verificada, ou reset de admin/CLI)."""
    uid = uuid.UUID(user_id)
    await pool().execute(
        """
        UPDATE dash_users SET totp_secret = NULL, totp_pending_secret = NULL, totp_enabled_at = NULL,
               totp_last_step = NULL, totp_recovery_hashes = '{}', updated_at = now()
        WHERE id = $1
        """,
        uid,
    )
    await pool().execute("DELETE FROM dash_mfa_challenges WHERE user_id = $1", uid)


# ---------------------------------------------------------------- desafio do login


async def create_challenge(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    uid = uuid.UUID(user_id)
    await pool().execute("DELETE FROM dash_mfa_challenges WHERE user_id = $1 OR expires_at <= now()", uid)
    await pool().execute(
        "INSERT INTO dash_mfa_challenges (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        _hash(token),
        uid,
        datetime.now(UTC) + CHALLENGE_TTL,
    )
    return token


async def challenge_user(token: str) -> dict | None:
    """Usuário do desafio ainda válido (não expirado, abaixo do teto de tentativas, conta ativa)."""
    row = await pool().fetchrow(
        """
        SELECT u.id, u.username, u.role
        FROM dash_mfa_challenges c JOIN dash_users u ON u.id = c.user_id
        WHERE c.token_hash = $1 AND c.expires_at > now() AND c.attempts < $2 AND u.disabled_at IS NULL
        """,
        _hash(token),
        CHALLENGE_MAX_ATTEMPTS,
    )
    return None if row is None else {"id": str(row["id"]), "username": row["username"], "role": row["role"]}


async def register_challenge_failure(token: str) -> None:
    await pool().execute("UPDATE dash_mfa_challenges SET attempts = attempts + 1 WHERE token_hash = $1", _hash(token))


async def consume_challenge(token: str) -> None:
    await pool().execute("DELETE FROM dash_mfa_challenges WHERE token_hash = $1", _hash(token))


async def cleanup_challenges() -> dict:
    deleted = await pool().fetchval(
        "WITH d AS (DELETE FROM dash_mfa_challenges WHERE expires_at <= now() RETURNING 1) SELECT count(*) FROM d"
    )
    return {"dash_mfa_challenges_expired_deleted": deleted}
