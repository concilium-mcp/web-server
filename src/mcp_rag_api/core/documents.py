"""Coleções e documentos: CRUD, versionamento, indexação em chunks e detecção de duplicatas."""

import re
import uuid
from typing import Any

import asyncpg
import numpy as np

from ..config import get_settings
from ..db import audit, pool, record, records
from ..security import KBError, NotFound, Principal, VersionConflict
from .chunking import chunk_text, content_hash, normalize
from .embeddings import get_embedder
from .wikilinks import resolve_pending, sync_wikilinks


def parse_uuid(value: str, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as e:
        raise KBError(f"{what} inválido: {value!r}") from e


# ---------------------------------------------------------------- coleções

# Nome de coleção aparece em URLs, atributos data-* e <option> da dash: sem HTML/JS,
# só letras (unicode), dígitos, "_" , "-" e espaço; começa em letra/dígito/_; até 80 chars.
COLLECTION_NAME_RE = re.compile(r"^[\w][\w \-]{0,79}$", re.UNICODE)


def validate_collection_name(name: str) -> str:
    """Normaliza e valida o nome de uma coleção. Rejeita com KBError (400) o que não serve."""
    cleaned = name.strip()
    if not COLLECTION_NAME_RE.fullmatch(cleaned):
        raise KBError(
            "Nome de coleção inválido: use de 1 a 80 caracteres, começando com letra, "
            "número ou '_' — permitidos também espaço e '-'."
        )
    return cleaned


async def list_collections(p: Principal) -> list[dict]:
    p.require("read")
    rows = await pool().fetch(
        """
        SELECT c.name, c.description, c.created_at,
               count(d.id) FILTER (WHERE d.status = 'active') AS documents
        FROM collections c LEFT JOIN documents d ON d.collection_id = c.id
        GROUP BY c.id ORDER BY c.name
        """
    )
    return [r for r in records(rows) if p.can_access_collection(r["name"])]


async def create_collection(p: Principal, name: str, description: str | None = None) -> dict:
    p.require("write")
    name = validate_collection_name(name)
    p.require_collection(name)
    async with pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO collections (name, description) VALUES ($1, $2) "
            "ON CONFLICT (name) DO UPDATE SET description = COALESCE(EXCLUDED.description, collections.description) "
            "RETURNING name, description, created_at",
            name,
            description,
        )
        await audit(conn, p.actor, "collection.upsert", name)
    return record(row)  # type: ignore[return-value]


async def _collection_id(conn: asyncpg.Connection, name: str) -> uuid.UUID:
    cid = await conn.fetchval("SELECT id FROM collections WHERE name = $1", name)
    if cid is None:
        raise NotFound(f"Coleção '{name}' não existe. Crie com create_collection.")
    return cid


# ---------------------------------------------------------------- indexação


async def _embed_chunks(title: str, content: str) -> list[tuple[int, str, int, np.ndarray]]:
    s = get_settings()
    chunks = chunk_text(content, s.chunk_words, s.chunk_overlap_words)
    if not chunks:
        raise KBError("Conteúdo vazio.")
    # O título entra no texto vetorizado para dar contexto a cada chunk.
    vectors = await get_embedder().embed([f"{title}\n\n{c.content}" for c in chunks], "document")
    return [(c.index, c.content, c.word_count, v) for c, v in zip(chunks, vectors, strict=True)]


def _centroid(vectors: list[np.ndarray]) -> np.ndarray:
    """Centroide (unitário) de um conjunto de embeddings: média normalizada."""
    centroid = np.mean(vectors, axis=0)
    norm = float(np.linalg.norm(centroid))
    return centroid / norm if norm > 0 else centroid


async def _write_chunks(conn: asyncpg.Connection, doc_id: uuid.UUID, chunks: list) -> None:
    await conn.execute("DELETE FROM chunks WHERE document_id = $1", doc_id)
    await conn.executemany(
        "INSERT INTO chunks (document_id, chunk_index, content, word_count, embedding) VALUES ($1, $2, $3, $4, $5)",
        [(doc_id, i, c, wc, v) for i, c, wc, v in chunks],
    )
    # Centroide alimenta a detecção de duplicatas e o grafo da dashboard via cosseno.
    await conn.execute(
        "UPDATE documents SET centroid = $2 WHERE id = $1", doc_id, _centroid([v for _, _, _, v in chunks])
    )


async def _find_similar(
    conn: asyncpg.Connection, collection_id: uuid.UUID, vector: np.ndarray, exclude: uuid.UUID | None
) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT d.id, d.title, 1 - (d.centroid <=> $2) AS similarity
        FROM documents d
        WHERE d.collection_id = $1 AND d.status = 'active' AND d.centroid IS NOT NULL
          AND ($3::uuid IS NULL OR d.id <> $3)
        ORDER BY d.centroid <=> $2 LIMIT 1
        """,
        collection_id,
        vector,
        exclude,
    )
    if row and row["similarity"] >= get_settings().duplicate_threshold:
        return {"document_id": str(row["id"]), "title": row["title"], "similarity": round(row["similarity"], 3)}
    return None


# ---------------------------------------------------------------- documentos

_DOC_COLUMNS = """
    d.id, c.name AS collection, d.external_id, d.title, d.source, d.metadata, d.tags, d.version,
    d.status, d.created_by, d.updated_by, d.created_at, d.updated_at
"""


async def _load_doc(conn: asyncpg.Connection, p: Principal, document_id: str, full: bool = False) -> dict:
    cols = _DOC_COLUMNS + (", d.content, d.content_hash" if full else "")
    row = await conn.fetchrow(
        f"SELECT {cols} FROM documents d JOIN collections c ON c.id = d.collection_id WHERE d.id = $1",
        parse_uuid(document_id, "document_id"),
    )
    if row is None:
        raise NotFound(f"Documento {document_id} não encontrado.")
    p.require_collection(row["collection"])
    return record(row)  # type: ignore[return-value]


async def get_document(p: Principal, document_id: str) -> dict:
    p.require("read")
    async with pool().acquire() as conn:
        doc = await _load_doc(conn, p, document_id, full=True)
    doc.pop("content_hash", None)
    return doc


async def list_documents(
    p: Principal,
    collection: str | None = None,
    tags: list[str] | None = None,
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    p.require("read")
    collections = p.collection_filter([collection] if collection else None)
    rows = await pool().fetch(
        f"""
        SELECT {_DOC_COLUMNS} FROM documents d JOIN collections c ON c.id = d.collection_id
        WHERE d.status = $1
          AND ($2::text[] IS NULL OR c.name = ANY($2))
          AND ($3::text[] IS NULL OR d.tags && $3)
        ORDER BY d.updated_at DESC LIMIT $4 OFFSET $5
        """,
        status,
        collections,
        tags or None,
        min(max(limit, 1), 100),
        max(offset, 0),
    )
    return records(rows)


async def add_document(
    p: Principal,
    collection: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
    external_id: str | None = None,
    force: bool = False,
) -> dict:
    p.require("write")
    p.require_collection(collection)
    content = normalize(content)
    title = title.strip()
    chunks = await _embed_chunks(title, content)
    async with pool().acquire() as conn:
        cid = await _collection_id(conn, collection)
        if not force:
            # duplicata é avaliada pelo centroide do documento, não pelo chunk 0:
            # o primeiro chunk pode ser o que mais difere (ex.: nova introdução).
            similar = await _find_similar(conn, cid, _centroid([v for _, _, _, v in chunks]), None)
            if similar:
                return {
                    "created": False,
                    "reason": "duplicate_suspected",
                    "similar_document": similar,
                    "hint": "Já existe conteúdo muito parecido. Use update_document nesse documento "
                    "ou repita add_document com force=true se for realmente outro conteúdo.",
                }
        async with conn.transaction():
            try:
                doc_id = await conn.fetchval(
                    """
                    INSERT INTO documents (collection_id, external_id, title, source, content, content_hash,
                                           metadata, tags, created_by, updated_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9) RETURNING id
                    """,
                    cid,
                    external_id,
                    title,
                    source,
                    content,
                    content_hash(title, content),
                    metadata or {},
                    tags or [],
                    p.actor,
                )
            except asyncpg.UniqueViolationError as e:
                raise KBError(f"external_id '{external_id}' já existe nessa coleção. Use upsert_document.") from e
            await conn.execute(
                "INSERT INTO document_versions (document_id, version, title, content, metadata, tags, "
                "changed_by, change_note) VALUES ($1, 1, $2, $3, $4, $5, $6, 'criação')",
                doc_id,
                title,
                content,
                metadata or {},
                tags or [],
                p.actor,
            )
            await _write_chunks(conn, doc_id, chunks)
            await sync_wikilinks(conn, doc_id, content, p.actor)
            await resolve_pending(conn, doc_id, title)
            await audit(conn, p.actor, "document.create", str(doc_id), collection=collection, title=title)
    return {"created": True, "document_id": str(doc_id), "version": 1, "chunks": len(chunks)}


async def update_document(
    p: Principal,
    document_id: str,
    change_note: str,
    content: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
    base_version: int | None = None,
) -> dict:
    """Atualiza o documento criando uma versão nova.

    Trava otimista: quando `base_version` vem informada, ela é a guarda do UPDATE
    (`WHERE version = base_version`); conflito vira `VersionConflict` com a versão
    atual. Sem `base_version`, a guarda é a versão lida — o conflito só aparece em race.
    """
    p.require("write")
    if not change_note or not change_note.strip():
        raise KBError("change_note é obrigatório: descreva o que mudou e por quê.")
    async with pool().acquire() as conn:
        current = await _load_doc(conn, p, document_id, full=True)
        if current["status"] != "active":
            raise KBError("Documento arquivado não pode ser editado.")
        if base_version is not None and base_version != current["version"]:
            raise VersionConflict(
                "O documento foi alterado por outro agente enquanto isso. Leia de novo e repita.",
                current_version=current["version"],
                actor=current["updated_by"],
            )
        new_title = (title or current["title"]).strip()
        new_content = normalize(content) if content is not None else current["content"]
        new_tags = tags if tags is not None else current["tags"]
        new_meta = {**current["metadata"], **metadata} if metadata is not None else current["metadata"]
        new_hash = content_hash(new_title, new_content)
        text_changed = new_hash != current["content_hash"]
        if (
            not text_changed
            and new_tags == current["tags"]
            and new_meta == current["metadata"]
            and (source is None or source == current["source"])
        ):
            return {"updated": False, "reason": "no_changes", "document_id": document_id, "version": current["version"]}

    chunks = await _embed_chunks(new_title, new_content) if text_changed else None
    doc_uuid = parse_uuid(document_id)
    expected_version = base_version if base_version is not None else current["version"]
    async with pool().acquire() as conn, conn.transaction():
        version = await conn.fetchval(
            """
            UPDATE documents SET title = $2, content = $3, content_hash = $4, tags = $5, metadata = $6,
                   source = COALESCE($7, source), version = version + 1, updated_by = $8, updated_at = now()
            WHERE id = $1 AND version = $9 RETURNING version
            """,
            doc_uuid,
            new_title,
            new_content,
            new_hash,
            new_tags,
            new_meta,
            source,
            p.actor,
            expected_version,
        )
        if version is None:
            row = await conn.fetchrow("SELECT version, updated_by FROM documents WHERE id = $1", doc_uuid)
            if row is None:
                raise NotFound(f"Documento {document_id} não encontrado.")
            raise VersionConflict(
                "O documento foi alterado por outro agente enquanto isso. Leia de novo e repita.",
                current_version=row["version"],
                actor=row["updated_by"],
            )
        await conn.execute(
            "INSERT INTO document_versions (document_id, version, title, content, metadata, tags, "
            "changed_by, change_note) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            doc_uuid,
            version,
            new_title,
            new_content,
            new_meta,
            new_tags,
            p.actor,
            change_note,
        )
        if chunks is not None:
            await _write_chunks(conn, doc_uuid, chunks)
            await sync_wikilinks(conn, doc_uuid, new_content, p.actor)
            if new_title != current["title"]:
                await resolve_pending(conn, doc_uuid, new_title)
        await audit(conn, p.actor, "document.update", document_id, version=version, note=change_note)
    return {"updated": True, "document_id": document_id, "version": version, "reindexed": chunks is not None}


async def upsert_document(
    p: Principal,
    collection: str,
    external_id: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
    change_note: str = "sincronização (upsert)",
) -> dict:
    p.require("write")
    p.require_collection(collection)
    async with pool().acquire() as conn:
        cid = await _collection_id(conn, collection)
        existing = await conn.fetchval(
            "SELECT id FROM documents WHERE collection_id = $1 AND external_id = $2", cid, external_id
        )
    if existing is None:
        return await add_document(
            p, collection, title, content, tags, metadata, source, external_id=external_id, force=True
        )
    return await update_document(
        p, str(existing), change_note, content=content, title=title, tags=tags, metadata=metadata, source=source
    )


async def archive_document(p: Principal, document_id: str, reason: str) -> dict:
    p.require("write")
    async with pool().acquire() as conn:
        await _load_doc(conn, p, document_id)
        async with conn.transaction():
            await conn.execute(
                "UPDATE documents SET status = 'archived', updated_by = $2, updated_at = now() WHERE id = $1",
                parse_uuid(document_id),
                p.actor,
            )
            await audit(conn, p.actor, "document.archive", document_id, reason=reason)
    return {"archived": True, "document_id": document_id}


async def move_document(p: Principal, document_id: str, collection: str) -> dict:
    """Move o documento para outra coleção (mesmo conteúdo e versão; fica registrado no audit)."""
    p.require("write")
    p.require_collection(collection)
    async with pool().acquire() as conn:
        doc = await _load_doc(conn, p, document_id)
        if doc["status"] != "active":
            raise KBError("Documento arquivado não pode ser movido.")
        if doc["collection"] == collection:
            return {"moved": False, "document_id": document_id, "collection": collection}
        cid = await _collection_id(conn, collection)
        async with conn.transaction():
            try:
                await conn.execute(
                    "UPDATE documents SET collection_id = $2, updated_by = $3, updated_at = now() WHERE id = $1",
                    parse_uuid(document_id),
                    cid,
                    p.actor,
                )
            except asyncpg.UniqueViolationError as e:
                raise KBError(f"Já existe um documento com o mesmo external_id em '{collection}'.") from e
            await audit(conn, p.actor, "document.move", document_id, source=doc["collection"], destination=collection)
    return {"moved": True, "document_id": document_id, "collection": collection}


async def get_document_version(p: Principal, document_id: str, version: int) -> dict:
    """Conteúdo de uma versão antiga (para comparar ou restaurar)."""
    p.require("read")
    async with pool().acquire() as conn:
        await _load_doc(conn, p, document_id)
        row = await conn.fetchrow(
            "SELECT version, title, content, tags, metadata, changed_by, change_note, created_at "
            "FROM document_versions WHERE document_id = $1 AND version = $2",
            parse_uuid(document_id),
            version,
        )
    if row is None:
        raise NotFound(f"Versão {version} do documento {document_id} não encontrada.")
    return record(row)  # type: ignore[return-value]


async def restore_document_version(p: Principal, document_id: str, version: int) -> dict:
    """Volta título, conteúdo e tags de uma versão antiga — como uma versão NOVA (o histórico não é apagado)."""
    old = await get_document_version(p, document_id, version)
    result = await update_document(
        p, document_id, f"restaurada a v{version}", content=old["content"], title=old["title"], tags=old["tags"]
    )
    return {**result, "restored_from": version}


async def document_history(p: Principal, document_id: str) -> list[dict]:
    p.require("read")
    async with pool().acquire() as conn:
        await _load_doc(conn, p, document_id)
        rows = await conn.fetch(
            "SELECT version, title, changed_by, change_note, created_at, length(content) AS content_chars "
            "FROM document_versions WHERE document_id = $1 ORDER BY version DESC",
            parse_uuid(document_id),
        )
    return records(rows)
