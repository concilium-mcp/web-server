"""API da dashboard web (prefixo /dash/api) — consumida pela SPA em /dashboard.

Autenticação própria (sessão da dash via cookie, ver core/dash_auth.py), separada do
Bearer da API pública. Guard aplicada a partir da fatia 2 (login); por ora as rotas
são abertas porque a dash ainda não tem usuários.
"""

from typing import Literal

from fastapi import APIRouter, Query

from .core import graph
from .core.documents import parse_uuid
from .db import pool, records
from .security import NotFound

router = APIRouter(prefix="/dash/api", tags=["dashboard"])


@router.get("/graph")
async def get_graph(
    collection: str | None = None,
    min_similarity: float = Query(default=0.7, ge=0.0, lt=1.0),
    k: int = Query(default=5, ge=1, le=20),
    level: Literal["documents", "chunks"] = "documents",
) -> dict:
    """Nós e arestas do grafo da base: vizinhança por cosseno (centroides ou chunks)."""
    return await graph.build_graph(level, collection, min_similarity, k)


@router.get("/documents/{document_id}")
async def get_document_detail(document_id: str) -> dict:
    """Painel lateral do grafo: documento com conteúdo, chunks e histórico de versões."""
    doc_uuid = parse_uuid(document_id, "document_id")
    async with pool().acquire() as conn:
        doc = await conn.fetchrow(
            """
            SELECT d.id, d.title, d.source, d.version, d.status, d.tags, d.metadata, d.content,
                   d.created_by, d.updated_by, d.created_at, d.updated_at, c.name AS collection
            FROM documents d
            JOIN collections c ON c.id = d.collection_id
            WHERE d.id = $1
            """,
            doc_uuid,
        )
        if doc is None:
            raise NotFound(f"Documento {document_id} não encontrado.")
        chunks = await conn.fetch(
            "SELECT chunk_index, word_count, content FROM chunks WHERE document_id = $1 ORDER BY chunk_index",
            doc_uuid,
        )
        versions = await conn.fetch(
            "SELECT version, changed_by, change_note, created_at FROM document_versions "
            "WHERE document_id = $1 ORDER BY version DESC",
            doc_uuid,
        )
    out = records([doc])[0]
    out["chunks"] = records(chunks)
    out["versions"] = records(versions)
    return out
