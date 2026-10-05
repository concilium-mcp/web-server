"""Links explícitos entre documentos: backlinks, links manuais e documentos relacionados.

Wikilinks ([[Título]]) são derivados do conteúdo em core/wikilinks.py; aqui ficam as operações
públicas usadas pela API, pelo MCP e pela dashboard. Documentos arquivados ou de coleções fora do
alcance do principal não aparecem.
"""

from ..db import audit, pool, records
from ..security import KBError, NotFound, Principal
from .documents import _load_doc, parse_uuid
from .wikilinks import sync_wikilinks

_OUTGOING_SQL = """
    SELECT l.id, l.kind, l.note, l.target_title, l.created_by, l.created_at,
           d.id AS document_id, d.title, c.name AS collection
    FROM document_links l
    LEFT JOIN documents d ON d.id = l.target_id
    LEFT JOIN collections c ON c.id = d.collection_id
    WHERE l.source_id = $1 AND (l.target_id IS NULL OR d.status = 'active')
    ORDER BY l.kind, coalesce(d.title, l.target_title)
"""

_BACKLINKS_SQL = """
    SELECT l.id, l.kind, l.note, l.created_by, l.created_at,
           d.id AS document_id, d.title, c.name AS collection
    FROM document_links l
    JOIN documents d ON d.id = l.source_id
    JOIN collections c ON c.id = d.collection_id
    WHERE l.target_id = $1 AND d.status = 'active'
    ORDER BY d.title
"""

_SEMANTIC_SQL = """
    SELECT d.id AS document_id, d.title, c.name AS collection,
           round((1 - (d.centroid <=> src.centroid))::numeric, 4)::float AS similarity
    FROM documents src
    JOIN documents d ON d.id <> src.id AND d.status = 'active' AND d.centroid IS NOT NULL
    JOIN collections c ON c.id = d.collection_id
    WHERE src.id = $1 AND src.centroid IS NOT NULL
      AND ($2::text[] IS NULL OR c.name = ANY($2))
    ORDER BY d.centroid <=> src.centroid
    LIMIT $3
"""


def _visible(p: Principal, rows: list[dict]) -> list[dict]:
    """Remove itens de coleções proibidas (pendentes não têm coleção e ficam)."""
    return [r for r in rows if r.get("collection") is None or p.can_access_collection(r["collection"])]


async def get_links(p: Principal, document_id: str) -> dict:
    """Links que saem do documento (inclui [[pendentes]]) e backlinks (quem aponta para ele)."""
    p.require("read")
    async with pool().acquire() as conn:
        doc = await _load_doc(conn, p, document_id)
        doc_uuid = parse_uuid(document_id)
        outgoing = records(await conn.fetch(_OUTGOING_SQL, doc_uuid))
        backlinks = records(await conn.fetch(_BACKLINKS_SQL, doc_uuid))
    for link in outgoing:
        link["pending"] = link["document_id"] is None
    return {
        "document": {"document_id": document_id, "title": doc["title"], "collection": doc["collection"]},
        "links": _visible(p, outgoing),
        "backlinks": _visible(p, backlinks),
    }


async def get_related(p: Principal, document_id: str, k: int = 5) -> dict:
    """Tudo o que se relaciona ao documento: links explícitos, backlinks e vizinhos semânticos."""
    out = await get_links(p, document_id)
    semantic = records(
        await pool().fetch(_SEMANTIC_SQL, parse_uuid(document_id), p.collection_filter(None), max(1, min(k, 20)))
    )
    out["semantic"] = semantic
    return out


async def link_documents(p: Principal, source_id: str, target_id: str, note: str | None = None) -> dict:
    """Cria um link manual source -> target (idempotente: se já existe, devolve o existente)."""
    p.require("write")
    if parse_uuid(source_id) == parse_uuid(target_id):
        raise KBError("Um documento não pode ser ligado a ele mesmo.")
    async with pool().acquire() as conn:
        source = await _load_doc(conn, p, source_id)
        target = await _load_doc(conn, p, target_id)
        if source["status"] != "active" or target["status"] != "active":
            raise KBError("Só documentos ativos podem ser ligados.")
        async with conn.transaction():
            link_id = await conn.fetchval(
                """
                INSERT INTO document_links (source_id, target_id, kind, note, created_by)
                VALUES ($1, $2, 'manual', $3, $4) ON CONFLICT DO NOTHING RETURNING id
                """,
                parse_uuid(source_id),
                parse_uuid(target_id),
                (note or "").strip() or None,
                p.actor,
            )
            created = link_id is not None
            if created:
                await audit(conn, p.actor, "document.link", source_id, target_id=target_id, note=note)
            else:
                link_id = await conn.fetchval(
                    "SELECT id FROM document_links WHERE source_id = $1 AND target_id = $2 AND kind = 'manual'",
                    parse_uuid(source_id),
                    parse_uuid(target_id),
                )
    return {"created": created, "link_id": link_id, "source": source["title"], "target": target["title"]}


async def unlink_documents(p: Principal, link_id: int) -> dict:
    """Remove um link manual. Wikilinks somem editando o texto do documento de origem."""
    p.require("write")
    async with pool().acquire() as conn:
        link = await conn.fetchrow("SELECT id, source_id, kind FROM document_links WHERE id = $1", link_id)
        if link is None:
            raise NotFound(f"Link {link_id} não encontrado.")
        if link["kind"] != "manual":
            raise KBError("Este link vem de um [[wikilink]] no texto: edite o documento de origem para removê-lo.")
        await _load_doc(conn, p, str(link["source_id"]))  # checa acesso à coleção da origem
        async with conn.transaction():
            await conn.execute("DELETE FROM document_links WHERE id = $1", link_id)
            await audit(conn, p.actor, "document.unlink", str(link["source_id"]), link_id=link_id)
    return {"deleted": True, "link_id": link_id}


async def relink_all(actor: str = "system:relink") -> dict:
    """Reprocessa os [[wikilinks]] de todos os documentos (backfill / após importações)."""
    docs = await pool().fetch("SELECT id, content FROM documents ORDER BY created_at")
    total = 0
    async with pool().acquire() as conn:
        for doc in docs:
            async with conn.transaction():
                total += await sync_wikilinks(conn, doc["id"], doc["content"], actor)
        pending = await conn.fetchval("SELECT count(*) FROM document_links WHERE target_id IS NULL")
    return {"documents": len(docs), "wikilinks": total, "pending": pending}
