"""Grafo da base RAG: nós ligados por similaridade de cosseno real dos embeddings.

Nível "documents" usa o centroide de cada documento (rápido, serve para bases grandes);
nível "chunks" usa os embeddings dos chunks diretamente (denso, limitado a MAX_CHUNK_NODES).
Arestas = top-k vizinhos de cada nó, filtrados por threshold de similaridade.
"""

from typing import Literal

from ..db import pool, records

MAX_CHUNK_NODES = 2000  # teto do nível "chunks" para não derrubar o navegador

_DOC_NODES_SQL = """
    SELECT d.id, d.title, c.name AS collection,
           (SELECT count(*) FROM chunks ch WHERE ch.document_id = d.id) AS chunks
    FROM documents d
    JOIN collections c ON c.id = d.collection_id
    WHERE d.status = 'active' AND d.centroid IS NOT NULL
      AND ($1::text IS NULL OR c.name = $1)
    ORDER BY c.name, d.title
"""

_DOC_EDGES_SQL = """
    WITH nodes AS (
        SELECT d.id, d.centroid
        FROM documents d
        JOIN collections c ON c.id = d.collection_id
        WHERE d.status = 'active' AND d.centroid IS NOT NULL
          AND ($1::text IS NULL OR c.name = $1)
    )
    SELECT n.id AS source, nb.id AS target,
           round((1 - (n.centroid <=> nb.centroid))::numeric, 4)::float AS similarity
    FROM nodes n
    CROSS JOIN LATERAL (
        SELECT d2.id, d2.centroid
        FROM documents d2
        JOIN collections c2 ON c2.id = d2.collection_id
        WHERE d2.status = 'active' AND d2.centroid IS NOT NULL AND d2.id <> n.id
          AND ($1::text IS NULL OR c2.name = $1)
          AND 1 - (d2.centroid <=> n.centroid) >= $3
        ORDER BY d2.centroid <=> n.centroid
        LIMIT $2
    ) nb
"""

_CHUNK_NODES_SQL = """
    SELECT c.id, c.document_id, c.chunk_index, d.title, col.name AS collection
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN collections col ON col.id = d.collection_id
    WHERE d.status = 'active'
      AND ($1::text IS NULL OR col.name = $1)
    ORDER BY c.id
    LIMIT $2
"""

_CHUNK_EDGES_SQL = """
    WITH nodes AS (
        SELECT c.id, c.embedding
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN collections col ON col.id = d.collection_id
        WHERE d.status = 'active'
          AND ($1::text IS NULL OR col.name = $1)
        ORDER BY c.id
        LIMIT $2
    )
    SELECT n.id AS source, nb.id AS target,
           round((1 - (n.embedding <=> nb.embedding))::numeric, 4)::float AS similarity
    FROM nodes n
    CROSS JOIN LATERAL (
        SELECT c2.id, c2.embedding
        FROM chunks c2
        JOIN documents d2 ON d2.id = c2.document_id
        JOIN collections col2 ON col2.id = d2.collection_id
        WHERE d2.status = 'active' AND c2.id <> n.id
          AND ($1::text IS NULL OR col2.name = $1)
          AND 1 - (c2.embedding <=> n.embedding) >= $4
        ORDER BY c2.embedding <=> n.embedding
        LIMIT $3
    ) nb
"""


def _dedupe_edges(rows: list[dict]) -> list[dict]:
    """A consulta devolve arestas dirigidas (cada nó lista seus top-k); mantém cada par uma única vez."""
    best: dict[frozenset[str], dict] = {}
    for r in rows:
        pair = frozenset((r["source"], r["target"]))
        if pair not in best or r["similarity"] > best[pair]["similarity"]:
            best[pair] = r
    return sorted(best.values(), key=lambda e: -e["similarity"])


async def build_graph(
    level: Literal["documents", "chunks"],
    collection: str | None,
    min_similarity: float,
    k: int,
) -> dict:
    if level == "chunks":
        nodes = records(await pool().fetch(_CHUNK_NODES_SQL, collection, MAX_CHUNK_NODES))
        edges = records(await pool().fetch(_CHUNK_EDGES_SQL, collection, MAX_CHUNK_NODES, k, min_similarity))
    else:
        nodes = records(await pool().fetch(_DOC_NODES_SQL, collection))
        edges = records(await pool().fetch(_DOC_EDGES_SQL, collection, k, min_similarity))
    # No nível chunks o LATERAL usa o índice da tabela inteira; descarta arestas para fora do conjunto.
    node_ids = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n["collection"]] = counts.get(n["collection"], 0) + 1
    return {
        "level": level,
        "collections": [{"name": name, "count": count} for name, count in sorted(counts.items())],
        "nodes": nodes,
        "edges": _dedupe_edges(edges),
    }
