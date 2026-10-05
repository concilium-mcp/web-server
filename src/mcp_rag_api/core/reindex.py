"""Reindexação: recalcula todos os embeddings com o provedor configurado.

Necessária ao trocar EMBEDDING_PROVIDER/EMBEDDING_MODEL — vetores de modelos diferentes não são
comparáveis, então chunks, centroides (grafo) e memórias dos agentes precisam ser refeitos juntos.
"""

from ..db import pool
from .documents import _embed_chunks, _write_chunks
from .embeddings import get_embedder


async def reindex_all(progress=None) -> dict:
    """Re-embeda chunks + centroide de todos os documentos (ativos e arquivados) e todas as memórias."""
    docs = await pool().fetch("SELECT id, title, content FROM documents ORDER BY created_at")
    for i, doc in enumerate(docs, 1):
        chunks = await _embed_chunks(doc["title"], doc["content"])
        async with pool().acquire() as conn, conn.transaction():
            await _write_chunks(conn, doc["id"], chunks)
        if progress:
            progress(f"documento {i}/{len(docs)}: {doc['title']}")

    memories = await pool().fetch("SELECT id, content FROM agent_memories ORDER BY created_at")
    if memories:
        vectors = await get_embedder().embed([m["content"] for m in memories], "document")
        await pool().executemany(
            "UPDATE agent_memories SET embedding = $2 WHERE id = $1",
            [(m["id"], v) for m, v in zip(memories, vectors, strict=True)],
        )
        if progress:
            progress(f"memórias: {len(memories)}")
    return {"documents": len(docs), "memories": len(memories)}
