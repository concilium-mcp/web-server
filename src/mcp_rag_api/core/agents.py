"""Registro de agentes: cadastro, versões, propostas, autonomia e chaves — tudo operável pelo MCP."""

from typing import Any

import asyncpg

from ..config import get_settings
from ..db import audit, pool, record, records
from ..security import KBError, NotFound, PermissionDenied, Principal, create_api_key, validate_scopes

# Campos que só mudam por ação de quem tem agents:manage — nunca por proposta do próprio agente.
PROTECTED_CONFIG_KEYS = {"auto_apply_updates"}
PROFILE_FIELDS = ("name", "description", "system_prompt", "config", "allowed_collections", "scopes")
_ELEVATED_SCOPES = {"admin", "agents:manage"}


async def resolve_agent(conn: asyncpg.Connection, p: Principal, slug: str | None) -> asyncpg.Record:
    """Agente alvo da operação. Um agente só acessa a si mesmo; agents:manage acessa qualquer um."""
    if not slug:
        if p.agent_slug is None:
            raise KBError("Informe agent_slug: esta chave não pertence a um agente.")
        slug = p.agent_slug
    if slug != p.agent_slug and not p.is_manager:
        raise PermissionDenied("Um agente só acessa o próprio perfil.")
    row = await conn.fetchrow("SELECT * FROM agents WHERE slug = $1", slug)
    if row is None:
        raise NotFound(f"Agente '{slug}' não encontrado.")
    return row


def _check_grant(p: Principal, scopes: list[str]) -> None:
    if _ELEVATED_SCOPES & set(scopes) and not p.has("admin"):
        raise PermissionDenied("Só uma chave admin pode dar os escopos 'admin' ou 'agents:manage' a um agente.")


def connect_command(slug: str, api_key: str) -> str:
    url = get_settings().public_url.rstrip("/")
    return f'claude mcp add --transport http kb-{slug} {url}/mcp --header "Authorization: Bearer {api_key}"'


def _profile(row: asyncpg.Record | dict) -> dict:
    r = record(row) if not isinstance(row, dict) else row
    keys = (
        "slug",
        "name",
        "description",
        "system_prompt",
        "config",
        "allowed_collections",
        "scopes",
        "version",
        "status",
        "created_at",
        "updated_at",
    )
    return {k: r[k] for k in keys if k in r}


async def _apply(
    conn: asyncpg.Connection,
    agent: asyncpg.Record,
    changes: dict[str, Any],
    actor: str,
    change_note: str,
    proposal_id: int | None = None,
) -> dict:
    """Aplica mudanças ao perfil, incrementa a versão e grava um snapshot completo em agent_versions."""
    new = {f: agent[f] for f in PROFILE_FIELDS}
    for f, v in changes.items():
        if f == "config":
            merged = dict(agent["config"])
            for k, val in v.items():
                if val is None:
                    merged.pop(k, None)
                else:
                    merged[k] = val
            new["config"] = merged
        else:
            new[f] = v
    row = await conn.fetchrow(
        """
        UPDATE agents SET name = $2, description = $3, system_prompt = $4, config = $5,
               allowed_collections = $6, scopes = $7, version = version + 1, updated_at = now()
        WHERE id = $1 AND version = $8 RETURNING *
        """,
        agent["id"],
        new["name"],
        new["description"],
        new["system_prompt"],
        new["config"],
        new["allowed_collections"],
        new["scopes"],
        agent["version"],
    )
    if row is None:
        raise KBError("O agente foi alterado ao mesmo tempo por outra operação. Tente de novo.")
    snapshot = (
        row["version"],
        row["name"],
        row["description"],
        row["system_prompt"],
        row["config"],
        row["allowed_collections"],
        row["scopes"],
    )
    if proposal_id is None:
        await conn.execute(
            """
            INSERT INTO agent_versions (agent_id, version, name, description, system_prompt, config,
                                        allowed_collections, scopes, change_note, proposed_by, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'applied')
            """,
            agent["id"],
            *snapshot,
            change_note,
            actor,
        )
    else:
        await conn.execute(
            """
            UPDATE agent_versions SET version = $2, name = $3, description = $4, system_prompt = $5,
                   config = $6, allowed_collections = $7, scopes = $8, status = 'applied',
                   reviewed_by = $9, reviewed_at = now()
            WHERE id = $1
            """,
            proposal_id,
            *snapshot,
            actor,
        )
    await audit(
        conn, actor, "agent.update", row["slug"], version=row["version"], note=change_note, fields=sorted(changes)
    )
    return _profile(row)


# ---------------------------------------------------------------- cadastro


async def create_agent(
    p: Principal,
    slug: str,
    name: str,
    system_prompt: str,
    description: str | None = None,
    config: dict[str, Any] | None = None,
    allowed_collections: list[str] | None = None,
    scopes: list[str] | None = None,
) -> dict:
    p.require("agents:manage")
    scopes = validate_scopes(scopes or ["read", "write"])
    _check_grant(p, scopes)
    config = {"auto_apply_updates": False, **(config or {})}
    async with pool().acquire() as conn, conn.transaction():
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO agents (slug, name, description, system_prompt, config, allowed_collections,
                                    scopes, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *
                """,
                slug.strip().lower(),
                name,
                description,
                system_prompt,
                config,
                allowed_collections or [],
                scopes,
                p.actor,
            )
        except asyncpg.UniqueViolationError as e:
            raise KBError(f"Já existe um agente com slug '{slug}'.") from e
        except asyncpg.CheckViolationError as e:
            raise KBError("slug inválido: use letras minúsculas, números e hífen (2–63 caracteres).") from e
        await conn.execute(
            """
            INSERT INTO agent_versions (agent_id, version, name, description, system_prompt, config,
                                        allowed_collections, scopes, change_note, proposed_by, status)
            VALUES ($1, 1, $2, $3, $4, $5, $6, $7, 'criação', $8, 'applied')
            """,
            row["id"],
            row["name"],
            row["description"],
            row["system_prompt"],
            row["config"],
            row["allowed_collections"],
            row["scopes"],
            p.actor,
        )
        key = await create_api_key(conn, label=f"agent:{row['slug']}", scopes=[], agent_id=row["id"])
        await audit(conn, p.actor, "agent.create", row["slug"])
    return {
        "agent": _profile(row),
        "api_key": key["api_key"],
        "api_key_notice": "Guarde a chave agora: ela não será exibida novamente.",
        "connect_command": connect_command(row["slug"], key["api_key"]),
    }


async def update_agent(
    p: Principal,
    slug: str,
    change_note: str,
    name: str | None = None,
    description: str | None = None,
    system_prompt: str | None = None,
    config: dict[str, Any] | None = None,
    allowed_collections: list[str] | None = None,
    scopes: list[str] | None = None,
) -> dict:
    p.require("agents:manage")
    changes: dict[str, Any] = {
        k: v
        for k, v in {
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "config": config,
            "allowed_collections": allowed_collections,
            "scopes": validate_scopes(scopes) if scopes is not None else None,
        }.items()
        if v is not None
    }
    if not changes:
        raise KBError("Nada para alterar.")
    if "scopes" in changes:
        _check_grant(p, changes["scopes"])
    async with pool().acquire() as conn, conn.transaction():
        agent = await resolve_agent(conn, p, slug)
        return await _apply(conn, agent, changes, p.actor, change_note)


async def set_agent_autonomy(p: Principal, slug: str, auto_apply_updates: bool, note: str | None = None) -> dict:
    p.require("agents:manage")
    state = "ligada" if auto_apply_updates else "desligada"
    async with pool().acquire() as conn, conn.transaction():
        agent = await resolve_agent(conn, p, slug)
        profile = await _apply(
            conn,
            agent,
            {"config": {"auto_apply_updates": auto_apply_updates}},
            p.actor,
            note or f"autoatualização {state}",
        )
    return {"slug": slug, "auto_apply_updates": auto_apply_updates, "version": profile["version"]}


async def get_agent(p: Principal, slug: str | None = None) -> dict:
    p.require("read")
    async with pool().acquire() as conn:
        agent = await resolve_agent(conn, p, slug)
        versions = await conn.fetch(
            "SELECT id, version, status, change_note, proposed_by, reviewed_by, created_at, reviewed_at "
            "FROM agent_versions WHERE agent_id = $1 ORDER BY created_at DESC LIMIT 20",
            agent["id"],
        )
        out = {"agent": _profile(agent), "history": records(versions)}
        if p.is_manager:
            keys = await conn.fetch(
                "SELECT id, prefix, label, created_at, last_used_at, revoked_at FROM api_keys "
                "WHERE agent_id = $1 ORDER BY created_at DESC",
                agent["id"],
            )
            out["api_keys"] = records(keys)
    return out


async def list_agents(p: Principal, include_archived: bool = False) -> list[dict]:
    p.require("read")
    rows = await pool().fetch(
        "SELECT slug, name, description, version, status, scopes, allowed_collections, config, updated_at "
        "FROM agents WHERE $1 OR status = 'active' ORDER BY slug",
        include_archived and p.is_manager,
    )
    items = records(rows)
    if not p.is_manager:  # agentes comuns só veem o básico dos colegas
        items = [{k: r[k] for k in ("slug", "name", "description")} for r in items]
    return items


async def clone_agent(
    p: Principal, source_slug: str, new_slug: str, name: str, overrides: dict[str, Any] | None = None
) -> dict:
    p.require("agents:manage")
    async with pool().acquire() as conn:
        src = await resolve_agent(conn, p, source_slug)
    overrides = overrides or {}
    unknown = set(overrides) - set(PROFILE_FIELDS)
    if unknown:
        raise KBError(f"Campos desconhecidos em overrides: {sorted(unknown)}")
    return await create_agent(
        p,
        slug=new_slug,
        name=name,
        system_prompt=overrides.get("system_prompt", src["system_prompt"]),
        description=overrides.get("description", src["description"]),
        config={**src["config"], "auto_apply_updates": False, **overrides.get("config", {})},
        allowed_collections=overrides.get("allowed_collections", list(src["allowed_collections"])),
        scopes=overrides.get("scopes", list(src["scopes"])),
    )


async def archive_agent(p: Principal, slug: str, reason: str) -> dict:
    p.require("agents:manage")
    async with pool().acquire() as conn, conn.transaction():
        agent = await resolve_agent(conn, p, slug)
        await conn.execute("UPDATE agents SET status = 'archived', updated_at = now() WHERE id = $1", agent["id"])
        revoked = await conn.fetchval(
            "WITH r AS (UPDATE api_keys SET revoked_at = now() WHERE agent_id = $1 AND revoked_at IS NULL "
            "RETURNING 1) SELECT count(*) FROM r",
            agent["id"],
        )
        await audit(conn, p.actor, "agent.archive", slug, reason=reason)
    return {"archived": True, "slug": slug, "keys_revoked": revoked}


async def restore_agent_version(p: Principal, slug: str, version: int, change_note: str | None = None) -> dict:
    p.require("agents:manage")
    async with pool().acquire() as conn, conn.transaction():
        agent = await resolve_agent(conn, p, slug)
        old = await conn.fetchrow(
            "SELECT * FROM agent_versions WHERE agent_id = $1 AND version = $2 AND status = 'applied'",
            agent["id"],
            version,
        )
        if old is None:
            raise NotFound(f"Versão {version} do agente '{slug}' não encontrada.")
        changes = {f: old[f] for f in PROFILE_FIELDS if old[f] is not None}
        # config restaurada por inteiro (inclusive chaves removidas depois)
        changes["config"] = {**{k: None for k in agent["config"]}, **(old["config"] or {})}
        return await _apply(conn, agent, changes, p.actor, change_note or f"restaurado da versão {version}")


# ---------------------------------------------------------------- chaves


async def issue_agent_key(p: Principal, slug: str, label: str | None = None) -> dict:
    p.require("agents:manage")
    async with pool().acquire() as conn, conn.transaction():
        agent = await resolve_agent(conn, p, slug)
        key = await create_api_key(conn, label=label or f"agent:{slug}", scopes=[], agent_id=agent["id"])
        await audit(conn, p.actor, "agent.key.issue", slug, prefix=key["prefix"])
    return {
        **key,
        "api_key_notice": "Guarde a chave agora: ela não será exibida novamente.",
        "connect_command": connect_command(slug, key["api_key"]),
    }


async def revoke_agent_key(p: Principal, key_prefix: str) -> dict:
    p.require("agents:manage")
    async with pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "UPDATE api_keys SET revoked_at = now() WHERE prefix = $1 AND revoked_at IS NULL RETURNING id",
            key_prefix,
        )
        if not rows:
            raise NotFound(f"Nenhuma chave ativa com prefixo '{key_prefix}'.")
        await audit(conn, p.actor, "key.revoke", key_prefix)
    return {"revoked": len(rows), "prefix": key_prefix}


# ---------------------------------------------------------------- propostas do próprio agente


async def propose_agent_update(
    p: Principal,
    change_note: str,
    system_prompt: str | None = None,
    description: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict:
    if p.agent_slug is None:
        raise KBError("propose_agent_update é para o próprio agente. Para cadastro use update_agent.")
    if not change_note or not change_note.strip():
        raise KBError("change_note é obrigatório: explique a mudança e o motivo.")
    ignored: list[str] = []
    if config:
        ignored = sorted(PROTECTED_CONFIG_KEYS & set(config))
        config = {k: v for k, v in config.items() if k not in PROTECTED_CONFIG_KEYS} or None
    changes = {
        k: v
        for k, v in {"system_prompt": system_prompt, "description": description, "config": config}.items()
        if v is not None
    }
    if not changes:
        raise KBError("Nenhuma mudança permitida na proposta" + (f" (ignorado: {ignored})" if ignored else "."))

    async with pool().acquire() as conn, conn.transaction():
        agent = await resolve_agent(conn, p, p.agent_slug)
        proposal_id = await conn.fetchval(
            """
            INSERT INTO agent_versions (agent_id, description, system_prompt, config, change_note,
                                        proposed_by, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'proposed') RETURNING id
            """,
            agent["id"],
            description,
            system_prompt,
            config,
            change_note,
            p.actor,
        )
        await audit(conn, p.actor, "agent.propose", agent["slug"], proposal_id=proposal_id)
        if agent["config"].get("auto_apply_updates"):
            profile = await _apply(conn, agent, changes, p.actor, change_note, proposal_id=proposal_id)
            return {
                "proposal_id": proposal_id,
                "status": "applied",
                "version": profile["version"],
                "ignored_fields": ignored,
            }
    return {
        "proposal_id": proposal_id,
        "status": "proposed",
        "ignored_fields": ignored,
        "message": "Proposta registrada; aguarda aprovação de quem gerencia os agentes.",
    }


async def list_proposals(p: Principal, slug: str | None = None) -> list[dict]:
    p.require("agents:manage")
    rows = await pool().fetch(
        """
        SELECT v.id AS proposal_id, a.slug, a.version AS current_version, v.change_note, v.proposed_by,
               v.system_prompt, v.description, v.config, v.created_at
        FROM agent_versions v JOIN agents a ON a.id = v.agent_id
        WHERE v.status = 'proposed' AND ($1::text IS NULL OR a.slug = $1)
        ORDER BY v.created_at
        """,
        slug,
    )
    return records(rows)


async def review_agent_update(p: Principal, proposal_id: int, approve: bool, note: str | None = None) -> dict:
    p.require("agents:manage")
    async with pool().acquire() as conn, conn.transaction():
        prop = await conn.fetchrow(
            "SELECT * FROM agent_versions WHERE id = $1 AND status = 'proposed' FOR UPDATE", proposal_id
        )
        if prop is None:
            raise NotFound(f"Proposta {proposal_id} não encontrada ou já revisada.")
        agent = await conn.fetchrow("SELECT * FROM agents WHERE id = $1", prop["agent_id"])
        if not approve:
            await conn.execute(
                "UPDATE agent_versions SET status = 'rejected', reviewed_by = $2, review_note = $3, "
                "reviewed_at = now() WHERE id = $1",
                proposal_id,
                p.actor,
                note,
            )
            await audit(conn, p.actor, "agent.proposal.reject", agent["slug"], proposal_id=proposal_id)
            return {"proposal_id": proposal_id, "status": "rejected"}
        changes = {k: prop[k] for k in ("system_prompt", "description", "config") if prop[k] is not None}
        if note:
            await conn.execute("UPDATE agent_versions SET review_note = $2 WHERE id = $1", proposal_id, note)
        profile = await _apply(conn, agent, changes, p.actor, prop["change_note"], proposal_id=proposal_id)
    return {"proposal_id": proposal_id, "status": "applied", "version": profile["version"]}
