"""[[Wikilinks]]: extração do conteúdo e sincronização com a tabela document_links.

Sem dependências de outros módulos do core: é chamado de dentro das transações de
documents.add_document/update_document. Operações públicas (links manuais, backlinks,
get_related) ficam em core/links.py.
"""

import re
import unicodedata
import uuid

import asyncpg

# [[Título]], [[Título|texto exibido]] e [[Título#seção]] (a seção é ignorada no v1)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+?)\s*(?:#[^\[\]|\n]*)?(?:\|[^\[\]\n]*)?\]\]")


def title_key(title: str) -> str:
    """Mesma normalização do kb_title_key() do banco: minúsculas, sem acento, espaços colapsados."""
    no_accents = "".join(c for c in unicodedata.normalize("NFKD", title) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accents.lower().strip())


def parse_wikilinks(content: str) -> list[str]:
    """Títulos citados como [[...]], na ordem em que aparecem, sem repetição (comparação sem acento)."""
    seen: set[str] = set()
    titles: list[str] = []
    for match in _WIKILINK_RE.finditer(content or ""):
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        key = title_key(title)
        if key and key not in seen:
            seen.add(key)
            titles.append(title)
    return titles


async def resolve_titles(conn: asyncpg.Connection, source_id: uuid.UUID, titles: list[str]) -> dict[str, uuid.UUID]:
    """Resolve vários títulos de uma vez (1 query): mesma regra de `resolve_title`.

    Retorna mapa title_key -> id do documento ativo encontrado.
    """
    if not titles:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (kb_title_key(d.title)) kb_title_key(d.title) AS key, d.id
        FROM documents d
        WHERE kb_title_key(d.title) = ANY($2::text[]) AND d.status = 'active' AND d.id <> $1
        ORDER BY kb_title_key(d.title),
                 (d.collection_id = (SELECT collection_id FROM documents WHERE id = $1)) DESC,
                 d.updated_at DESC
        """,
        source_id,
        [title_key(t) for t in titles],
    )
    return {r["key"]: r["id"] for r in rows}


async def resolve_title(conn: asyncpg.Connection, source_id: uuid.UUID, title: str) -> uuid.UUID | None:
    """Documento ativo com esse título: prefere a mesma coleção da origem, depois o mais recente."""
    resolved = await resolve_titles(conn, source_id, [title])
    return resolved.get(title_key(title))


async def sync_wikilinks(conn: asyncpg.Connection, doc_id: uuid.UUID, content: str, actor: str) -> int:
    """Refaz os links kind='wikilink' do documento a partir do texto (links manuais não são tocados)."""
    await conn.execute("DELETE FROM document_links WHERE source_id = $1 AND kind = 'wikilink'", doc_id)
    titles = parse_wikilinks(content)
    resolved = await resolve_titles(conn, doc_id, titles)
    count = 0
    for title in titles:
        # ON CONFLICT: dois [[...]] diferentes podem cair no mesmo destino
        await conn.execute(
            """
            INSERT INTO document_links (source_id, target_id, target_title, kind, created_by)
            VALUES ($1, $2, $3, 'wikilink', $4) ON CONFLICT DO NOTHING
            """,
            doc_id,
            resolved.get(title_key(title)),
            title,
            actor,
        )
        count += 1
    return count


async def resolve_pending(conn: asyncpg.Connection, doc_id: uuid.UUID, title: str) -> int:
    """Um documento ganhou esse título (criado/renomeado): liga os [[...]] pendentes que o citavam."""
    # se a origem já aponta para este documento, o pendente seria duplicata: remove antes
    await conn.execute(
        """
        DELETE FROM document_links p
        WHERE p.target_id IS NULL AND kb_title_key(p.target_title) = kb_title_key($2)
          AND (p.source_id = $1 OR EXISTS (
                SELECT 1 FROM document_links l
                WHERE l.source_id = p.source_id AND l.target_id = $1 AND l.kind = p.kind))
        """,
        doc_id,
        title,
    )
    result = await conn.execute(
        """
        UPDATE document_links SET target_id = $1
        WHERE target_id IS NULL AND kb_title_key(target_title) = kb_title_key($2) AND source_id <> $1
        """,
        doc_id,
        title,
    )
    return int(result.split()[-1])
