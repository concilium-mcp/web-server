"""Resumo do projeto para o início da sessão (plan-web-34).

Cada coleção pode marcar uma nota como resumo (`collections.brief_document_id`). O texto montado
aqui é o que o plugin do Claude Code injeta no começo da sessão e o que a tool `get_project_brief`
devolve: o resumo (com teto de tokens), onde está o resto (pastas com contagem, sem conteúdo),
opcionalmente a última sessão e as tarefas do agente, e como pedir detalhes. O resto vem sob demanda.
"""

from datetime import UTC, datetime

from ..config import get_settings
from ..db import audit, pool
from ..security import KBError, NotFound, Principal
from . import documents, folders
from .tokens import estimate_tokens, trim_to_tokens

STALE_DAYS = 60
MAX_FOLDERS_LISTED = 30
MAX_TASKS_LISTED = 15


async def set_brief(p: Principal, collection: str, document_id: str | None) -> dict:
    """Marca (ou, com None, desmarca) a nota que é o resumo da coleção. A nota precisa ser dela."""
    p.require("write")
    p.require_collection(collection)
    doc_uuid = documents.parse_uuid(document_id, "document_id") if document_id else None
    async with pool().acquire() as conn:
        async with conn.transaction():
            col = await conn.fetchrow("SELECT id FROM collections WHERE name = $1 FOR UPDATE", collection)
            if col is None:
                raise NotFound(f"Coleção '{collection}' não encontrada.")
            if doc_uuid is not None:
                owner = await conn.fetchval(
                    "SELECT collection_id FROM documents WHERE id = $1 AND status = 'active'", doc_uuid
                )
                if owner is None:
                    raise NotFound(f"Documento {document_id} não encontrado.")
                if owner != col["id"]:
                    raise KBError("O resumo precisa ser uma nota da própria pasta principal.")
            await conn.execute("UPDATE collections SET brief_document_id = $2 WHERE id = $1", col["id"], doc_uuid)
            await audit(conn, p.actor, "collection.brief", collection, document_id=document_id)
    return {"collection": collection, "brief_document_id": document_id}


async def brief_document_ids(collection_names: list[str] | None = None) -> dict[str, str]:
    """{coleção: id da nota de resumo} — para a dash marcar o selo na árvore."""
    rows = await pool().fetch(
        "SELECT name, brief_document_id FROM collections "
        "WHERE brief_document_id IS NOT NULL AND ($1::text[] IS NULL OR name = ANY($1))",
        collection_names,
    )
    return {r["name"]: str(r["brief_document_id"]) for r in rows}


def _notes(n: int) -> str:
    return "1 nota" if n == 1 else f"{n} notas"


async def _folder_lines(p: Principal, collection: str) -> list[str]:
    tree = await folders.location_tree(p)
    mine = sorted((f for f in tree["folders"] if f["collection"] == collection), key=lambda f: f["path"])
    total = next((c["documents"] for c in tree["collections"] if c["name"] == collection), 0)
    lines = [f"- {collection} (tudo): {_notes(total)}"]
    for f in mine[:MAX_FOLDERS_LISTED]:
        lines.append(f"- {f['path']}: {_notes(f['total'])}")
    if len(mine) > MAX_FOLDERS_LISTED:
        lines.append(f"- … e mais {len(mine) - MAX_FOLDERS_LISTED} pastas (list_folders)")
    return lines


async def get_brief(p: Principal, collection: str, agent_slug: str | None = None) -> dict:
    """Texto Markdown pronto para injetar + metadados (documento, data, tamanho, se cortou)."""
    p.require("read")
    p.require_collection(collection)
    settings = get_settings()
    async with pool().acquire() as conn:
        col = await conn.fetchrow(
            "SELECT c.id, c.name, c.brief_document_id, d.title, d.content, d.updated_at "
            "FROM collections c LEFT JOIN documents d ON d.id = c.brief_document_id AND d.status = 'active' "
            "WHERE c.name = $1",
            collection,
        )
        if col is None:
            raise NotFound(f"Coleção '{collection}' não encontrada.")
        session = tasks = None
        if agent_slug:
            agent = await conn.fetchrow(
                "SELECT id, allowed_collections FROM agents WHERE slug = $1 AND status = 'active'", agent_slug
            )
            if agent is None:
                raise NotFound(f"Agente '{agent_slug}' não encontrado.")
            # só o próprio agente ou quem gerencia agentes lê a sessão e as tarefas dele
            if p.agent_slug != agent_slug and not p.is_manager:
                raise KBError("Sessões e tarefas de outro agente exigem o escopo agents:manage.")
            session = await conn.fetchrow(
                "SELECT summary, next_steps, ended_at FROM agent_sessions WHERE agent_id = $1 "
                "ORDER BY ended_at DESC LIMIT 1",
                agent["id"],
            )
            tasks = await conn.fetch(
                "SELECT title, status FROM agent_tasks WHERE agent_id = $1 AND status IN ('open', 'in_progress') "
                "ORDER BY due_at NULLS LAST, created_at LIMIT $2",
                agent["id"],
                MAX_TASKS_LISTED,
            )

    folder_lines = await _folder_lines(p, collection)
    parts: list[str] = []
    truncated = False
    brief_id = str(col["brief_document_id"]) if col["content"] is not None else None
    if brief_id:
        updated: datetime = col["updated_at"]
        age = (datetime.now(UTC) - updated).days
        head = f"# {collection} — resumo do projeto (Concilium, editado em {updated.date().isoformat()})"
        if age > STALE_DAYS:
            head += (
                f"\n\n> Atenção: este resumo não é editado há {age} dias; confira o que mudou antes de confiar nele."
            )
        body, truncated = trim_to_tokens(col["content"].strip(), settings.brief_max_tokens)
        parts.append(head)
        parts.append(body.rstrip())
        if truncated:
            parts.append("> Resumo maior que o teto (BRIEF_MAX_TOKENS): o restante foi cortado; encurte a nota.")
    else:
        parts.append(f"# {collection} — contexto do Concilium")
        parts.append("Esta pasta principal ainda não tem nota de resumo marcada.")
    if folder_lines:
        parts.append("## Onde está o resto\n\n" + "\n".join(folder_lines))
    if session is not None:
        text = f"## Sua última sessão ({session['ended_at'].date().isoformat()})\n\n{session['summary']}"
        if session["next_steps"]:
            text += f"\n\nPróximos passos: {session['next_steps']}"
        parts.append(text)
    if tasks:
        parts.append("## Tarefas abertas\n\n" + "\n".join(f"- [{t['status']}] {t['title']}" for t in tasks))
    parts.append(
        f'Detalhes sob demanda: search_knowledge com collections=["{collection}"]; '
        'get_document com outline=true e depois section="<título>".'
    )
    text = "\n\n".join(parts)
    return {
        "collection": collection,
        "brief_document_id": brief_id,
        "text": text,
        "tokens_estimate": estimate_tokens(text),
        "truncated": truncated,
    }


async def brief_for_agent_profile(p: Principal, allowed_collections: list[str] | None) -> dict | None:
    """Resumo da primeira coleção permitida do agente que tiver um (para o load_agent do MCP)."""
    for name in allowed_collections or []:
        try:
            data = await get_brief(p, name)
        except KBError:
            continue
        if data["brief_document_id"]:
            return {"collection": name, "text": data["text"]}
    return None
