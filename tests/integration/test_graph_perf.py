"""Grafo em memória (plan-web-22): mesmas arestas que a consulta LATERAL antiga do pgvector (oráculo
abaixo) e cache que se invalida quando a base muda."""

import pytest

from mcp_rag_api import db
from mcp_rag_api.core import documents, graph, links
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN

COLLECTION = "gp-perf"
TOPICS = [
    "reembolso pedido portal prazo dias compra cliente",
    "reembolso prazo cliente pedido cancelamento estorno",
    "agenda consulta paciente horario medico retorno",
    "agenda paciente horario retorno confirmacao consulta",
    "deploy coolify imagem registry pipeline main",
    "pipeline registry imagem deploy webhook coolify",
    "senha usuario login sessao cookie dashboard",
    "login sessao usuario senha admin papel",
]

# a consulta antiga, mantida só como oráculo da equivalência
_ORACLE_SQL = """
    WITH nodes AS (
        SELECT d.id, d.centroid FROM documents d JOIN collections c ON c.id = d.collection_id
        WHERE d.status = 'active' AND d.centroid IS NOT NULL AND c.name = $1
    )
    SELECT n.id::text AS source, nb.id::text AS target, (1 - (n.centroid <=> nb.centroid))::float AS similarity
    FROM nodes n
    CROSS JOIN LATERAL (
        SELECT d2.id, d2.centroid FROM documents d2 JOIN collections c2 ON c2.id = d2.collection_id
        WHERE d2.status = 'active' AND d2.centroid IS NOT NULL AND d2.id <> n.id AND c2.name = $1
          AND 1 - (d2.centroid <=> n.centroid) >= $3
        ORDER BY d2.centroid <=> n.centroid LIMIT $2
    ) nb
"""


@pytest.fixture(scope="module")
async def base_docs():
    await documents.create_collection(ADMIN, COLLECTION)
    ids = []
    for i, text in enumerate(TOPICS):
        r = await documents.add_document(ADMIN, COLLECTION, f"Doc {i}", text, force=True)
        ids.append(r["document_id"])
    return ids


# Equivalência por VALORES: para cada nó, as k similaridades dos vizinhos (ordenadas) são as mesmas da
# consulta antiga. Comparar QUAIS vizinhos seria frágil: com o embedder fake há empates exatos (ex.: 0,125
# entre vários pares) e o desempate entre empatados é arbitrário — no pgvector e aqui.
@pytest.mark.parametrize(("k", "min_sim"), [(1, 0.0), (3, 0.0), (5, 0.2), (7, 0.5), (20, 0.0)])
async def test_documents_level_matches_old_pgvector_query(base_docs, k, min_sim):
    oracle: dict[str, list[float]] = {}
    for r in await db.pool().fetch(_ORACLE_SQL, COLLECTION, k, min_sim):
        oracle.setdefault(r["source"], []).append(round(r["similarity"], 4))
    base = await graph._base("documents", COLLECTION)
    got: dict[str, list[float]] = {}
    for i, node in enumerate(base.nodes):
        sims = [round(float(s), 4) for s in base.neighbor_sim[i, :k] if s >= min_sim]
        if sims:
            got[node["id"]] = sims
    assert got == {src: sorted(v, reverse=True) for src, v in oracle.items()}
    # e o build_graph usa exatamente esses vizinhos: cada aresta semântica é um deles
    edges = (await graph.build_graph("documents", COLLECTION, min_sim, k))["edges"]
    assert all(e["similarity"] >= round(min_sim, 4) for e in edges if e["kind"] == "semantic")


async def test_cache_reuses_and_invalidates(base_docs):
    graph.clear_cache()
    first = await graph.build_graph("documents", COLLECTION, 0.0, 3)
    key = ("documents", COLLECTION, None)  # (nível, coleção, pastas)
    cached = graph._cache[key]
    await graph.build_graph("documents", COLLECTION, 0.4, 2)  # outro slider: mesmo cálculo, só filtra
    assert graph._cache[key] is cached

    extra = await documents.add_document(ADMIN, COLLECTION, "Doc extra", "reembolso estorno cliente portal", force=True)
    after_add = await graph.build_graph("documents", COLLECTION, 0.0, 3)
    assert graph._cache[key] is not cached
    assert len(after_add["nodes"]) == len(first["nodes"]) + 1

    cached = graph._cache[key]
    await links.link_documents(ADMIN, base_docs[0], base_docs[4], note="perf")  # link manual muda o grafo
    linked = await graph.build_graph("documents", COLLECTION, 0.99, 1)
    assert graph._cache[key] is not cached
    assert any(e["kind"] == "link" for e in linked["edges"])

    await documents.archive_document(ADMIN, extra["document_id"], "teste")
    after_archive = await graph.build_graph("documents", COLLECTION, 0.0, 3)
    assert extra["document_id"] not in {n["id"] for n in after_archive["nodes"]}


async def test_chunks_level_has_no_vectors_and_only_internal_edges(base_docs):
    g = await graph.build_graph("chunks", COLLECTION, 0.0, 3)
    ids = {n["id"] for n in g["nodes"]}
    assert g["nodes"] and all("vector" not in n for n in g["nodes"])
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
