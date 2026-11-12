"""Integrações dos quadros (plan-web-37d): contas conectadas (Trello agora; Notion e outros depois).

Credenciais cifradas com AES-GCM (INTEGRATIONS_ENCRYPTION_KEY), o id da integração como dado autenticado
(copiar o valor para outra linha não decifra). Sem a chave, conectar é recusado. A API nunca devolve as
credenciais: só o rótulo, a conta do lado de lá e o estado (último ok, último erro).
"""

import base64
import hashlib
import json
import secrets
import uuid
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings
from ..db import audit, pool, record, records
from ..security import KBError, NotFound, Principal
from .documents import parse_uuid

ENC_PREFIX = "enc:v1:"
PROVIDERS = ("trello", "notion")


def _cipher() -> AESGCM:
    key = get_settings().integrations_encryption_key
    if not key or len(key) < 32:
        raise KBError(
            "Defina INTEGRATIONS_ENCRYPTION_KEY (32+ caracteres, ex.: `openssl rand -base64 32`) no servidor "
            "antes de conectar uma integração: as credenciais só são guardadas cifradas."
        )
    return AESGCM(hashlib.sha256(b"concilium-integrations-v1\0" + key.encode()).digest())


def seal(data: dict[str, Any], integration_id: uuid.UUID) -> str:
    nonce = secrets.token_bytes(12)
    sealed = _cipher().encrypt(nonce, json.dumps(data).encode(), str(integration_id).encode())
    return ENC_PREFIX + base64.b64encode(nonce + sealed).decode()


def unseal(stored: str, integration_id: uuid.UUID) -> dict[str, Any]:
    if not stored.startswith(ENC_PREFIX):
        raise KBError("Credencial da integração em formato desconhecido; conecte de novo.")
    raw = base64.b64decode(stored[len(ENC_PREFIX) :])
    try:
        return json.loads(_cipher().decrypt(raw[:12], raw[12:], str(integration_id).encode()))
    except InvalidTag:
        raise KBError(
            "Não consegui decifrar a credencial: INTEGRATIONS_ENCRYPTION_KEY mudou? Conecte de novo."
        ) from None


def _public(row: Any) -> dict:
    out = record(row) or {}
    out.pop("credentials", None)
    return out


async def list_integrations(p: Principal) -> list[dict]:
    p.require("read")
    rows = await pool().fetch(
        "SELECT i.*, (SELECT count(*) FROM board_links l WHERE l.integration_id = i.id) AS boards "
        "FROM integrations i WHERE i.disabled_at IS NULL ORDER BY i.created_at"
    )
    return [_public(r) for r in rows]


async def save_integration(p: Principal, provider: str, label: str, credentials: dict[str, Any], account: dict) -> dict:
    """Grava (ou troca as credenciais de) uma integração do provedor — uma conta por provedor por enquanto."""
    p.require("admin")
    if provider not in PROVIDERS:
        raise KBError(f"Provedor desconhecido: {provider}.")
    async with pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT id FROM integrations WHERE provider = $1 AND disabled_at IS NULL ORDER BY created_at LIMIT 1",
            provider,
        )
        iid = row["id"] if row else uuid.uuid4()
        sealed = seal(credentials, iid)
        if row:
            await conn.execute(
                "UPDATE integrations SET label = $2, credentials = $3, account = $4, last_ok_at = now(), "
                "last_error = NULL WHERE id = $1",
                iid,
                label,
                sealed,
                account,
            )
        else:
            await conn.execute(
                "INSERT INTO integrations (id, provider, label, credentials, account, created_by, last_ok_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, now())",
                iid,
                provider,
                label,
                sealed,
                account,
                p.actor,
            )
        await audit(conn, p.actor, "integration.save", str(iid), provider=provider, label=label)
    return await get_integration(p, str(iid))


async def get_integration(p: Principal, integration_id: str) -> dict:
    p.require("read")
    row = await pool().fetchrow(
        "SELECT * FROM integrations WHERE id = $1", parse_uuid(integration_id, "integration_id")
    )
    if row is None or row["disabled_at"] is not None:
        raise NotFound("Integração não encontrada.")
    return _public(row)


async def credentials_of(integration_id: uuid.UUID) -> tuple[dict, dict]:
    """(credenciais decifradas, linha pública) — só para o código do provedor, nunca para a API."""
    row = await pool().fetchrow("SELECT * FROM integrations WHERE id = $1 AND disabled_at IS NULL", integration_id)
    if row is None:
        raise NotFound("Integração não encontrada ou desligada.")
    return unseal(row["credentials"], row["id"]), _public(row)


async def disconnect(p: Principal, integration_id: str) -> dict:
    """Desliga a integração: os quadros daqui ficam (param de espelhar); as credenciais são apagadas."""
    p.require("admin")
    iid = parse_uuid(integration_id, "integration_id")
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE integrations SET disabled_at = now(), credentials = 'apagada' "
            "WHERE id = $1 AND disabled_at IS NULL",
            iid,
        )
        n = await conn.execute("UPDATE board_links SET mode = 'import' WHERE integration_id = $1", iid)
        await audit(conn, p.actor, "integration.disconnect", str(iid))
    return {"disconnected": True, "boards_unmirrored": int(n.split()[-1])}


async def mark(integration_id: uuid.UUID, error: str | None) -> None:
    if error:
        await pool().execute("UPDATE integrations SET last_error = $2 WHERE id = $1", integration_id, error[:500])
    else:
        await pool().execute(
            "UPDATE integrations SET last_ok_at = now(), last_error = NULL WHERE id = $1", integration_id
        )


async def board_link(board_id: uuid.UUID) -> dict | None:
    row = await pool().fetchrow(
        "SELECT l.*, i.provider, i.label FROM board_links l JOIN integrations i ON i.id = l.integration_id "
        "WHERE l.board_id = $1 AND i.disabled_at IS NULL",
        board_id,
    )
    return record(row)


async def links_for(board_ids: list[uuid.UUID]) -> dict[str, dict]:
    rows = await pool().fetch(
        "SELECT l.board_id, l.mode, l.external_url, l.last_sync_at, l.last_error, i.provider FROM board_links l "
        "JOIN integrations i ON i.id = l.integration_id WHERE l.board_id = ANY($1) AND i.disabled_at IS NULL",
        board_ids,
    )
    return {str(r["board_id"]): r for r in records(rows)}
