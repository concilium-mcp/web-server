"""Busca híbrida: vetorial (cosseno) + full-text (português), combinadas por Reciprocal Rank Fusion."""

from ..db import pool, records
from ..security import Principal
from .embeddings import get_embedder

RRF_K = 60

_SEARCH_SQL = """
WITH filtered AS (
    SELECT c.id, c.embedding, c.tsv
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN collections col ON col.id = d.collection_id
    WHERE d.status = 'active'
      AND ($2::text[] IS NULL OR col.name = ANY($2))
      AND ($3::text[] IS NULL OR d.tags && $3)
      AND ($4::jsonb IS NULL OR d.metadata @> $4)
),
vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> $1) AS rank
    FROM filtered ORDER BY embedding <=> $1 LIMIT $5
),
fts AS (
    SELECT f.id, row_number() OVER (ORDER BY ts_rank_cd(f.tsv, q) DESC) AS rank
    FROM filtered f, websearch_to_tsquery('portuguese', $6) q
    WHERE f.tsv @@ q
    ORDER BY ts_rank_cd(f.tsv, q) DESC LIMIT $5
),
fused AS (
    SELECT id, sum(1.0 / ($7 + rank)) AS score
    FROM (SELECT * FROM vec UNION ALL SELECT * FROM fts) u
    GROUP BY id
)
SELECT d.id AS document_id, d.title, col.name AS collection, d.source, d.version, d.tags,
       c.chunk_index, c.content, round(f.score::numeric, 5)::float AS score,
       round((1 - (c.embedding <=> $1))::numeric, 4)::float AS similarity
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN documents d ON d.id = c.document_id
JOIN collections col ON col.id = d.collection_id
ORDER BY f.score DESC
LIMIT $8
"""


async def search_knowledge(
    p: Principal,
    query: str,
    collections: list[str] | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    top_k: int = 5,
) -> list[dict]:
    p.require("read")
    effective = p.collection_filter(collections)
    top_k = min(max(top_k, 1), 50)
    vector = await get_embedder().embed_one(query, "query")
    rows = await pool().fetch(
        _SEARCH_SQL,
        vector,
        effective,
        tags or None,
        metadata or None,
        max(top_k * 4, 20),  # candidatos por ramo antes da fusão
        query,
        RRF_K,
        top_k,
    )
    return records(rows)
