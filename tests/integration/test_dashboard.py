"""Dashboard (plan-web-01): centroide, grafo por cosseno, auth da dash, gestão e extras.

Roda contra Postgres real (ver tests/integration/conftest.py). Os endpoints são
exercitados via ASGITransport no mesmo loop da fixture de banco — o pool já está
inicializado pelo conftest, então o lifespan do app não precisa rodar.

O vocabulário dos documentos aqui é propositalmente disjunto do test_flows.py:
o banco é compartilhado entre os módulos e a busca híbrida lá não filtra collection.
"""

import uuid

import httpx
import numpy as np
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import documents
from mcp_rag_api.main import app
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN

# A e B compartilham quase todas as palavras (cosseno alto); C é distante de ambos.
FONTE_A = "alfa bravo charlie delta echo foxtrot"
FONTE_B = "alfa bravo charlie delta echo foxtrot golf hotel"
FONTE_C = "mike november oscar papa quebec romeo"


@pytest.fixture(scope="module")
async def seeded_docs():
    await documents.create_collection(ADMIN, "grafos")
    a = await documents.add_document(ADMIN, "grafos", "Alfa", FONTE_A)
    b = await documents.add_document(ADMIN, "grafos", "Bravo", FONTE_B, force=True)
    c = await documents.add_document(ADMIN, "grafos", "Mike", FONTE_C, force=True)
    return {"a": a["document_id"], "b": b["document_id"], "c": c["document_id"]}


async def dash_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dash.test")


async def test_centroid_written_on_ingest_and_reindex(seeded_docs):
    row = await db.pool().fetchrow("SELECT centroid FROM documents WHERE id = $1", uuid.UUID(seeded_docs["a"]))
    assert row["centroid"] is not None
    norm = float(np.linalg.norm(row["centroid"].to_numpy().astype(np.float32)))
    assert norm == pytest.approx(1.0, abs=1e-3)

    before = row["centroid"].to_numpy().astype(np.float32).copy()
    await documents.update_document(ADMIN, seeded_docs["a"], "conteúdo mudou", content=FONTE_C + " outro")
    row = await db.pool().fetchrow("SELECT centroid FROM documents WHERE id = $1", uuid.UUID(seeded_docs["a"]))
    assert not np.allclose(before, row["centroid"].to_numpy().astype(np.float32))
    # restaura o conteúdo original para os demais testes
    await documents.update_document(ADMIN, seeded_docs["a"], "restaurando", content=FONTE_A)


async def test_graph_endpoint_documents_level(seeded_docs):
    async with await dash_client() as client:
        resp = await client.get(
            "/dash/api/graph", params={"collection": "grafos", "min_similarity": 0.5, "k": 3}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] == "documents"
    assert {n["id"] for n in data["nodes"]} == set(seeded_docs.values())
    assert {c["name"] for c in data["collections"]} == {"grafos"}
    assert next(n for n in data["nodes"] if n["id"] == seeded_docs["a"])["chunks"] == 1

    pairs = {frozenset((e["source"], e["target"])) for e in data["edges"]}
    ab = frozenset((seeded_docs["a"], seeded_docs["b"]))
    assert ab in pairs
    assert all(seeded_docs["c"] not in p for p in pairs)
    edge = next(e for e in data["edges"] if frozenset((e["source"], e["target"])) == ab)
    assert edge["similarity"] >= 0.7  # conteúdo quase idêntico → cosseno alto

    # threshold alto demais: nenhuma aresta passa
    async with await dash_client() as client:
        resp = await client.get("/dash/api/graph", params={"collection": "grafos", "min_similarity": 0.95})
    assert resp.json()["edges"] == []


async def test_graph_endpoint_chunks_level(seeded_docs):
    async with await dash_client() as client:
        resp = await client.get(
            "/dash/api/graph",
            params={"level": "chunks", "collection": "grafos", "min_similarity": 0.5, "k": 3},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] == "chunks"
    assert len(data["nodes"]) == 3  # 1 chunk por documento
    assert all(n["document_id"] in set(seeded_docs.values()) for n in data["nodes"])

    by_doc = {}
    for n in data["nodes"]:
        by_doc.setdefault(n["document_id"], []).append(n["id"])
    pairs = {frozenset((e["source"], e["target"])) for e in data["edges"]}
    assert frozenset((by_doc[seeded_docs["a"]][0], by_doc[seeded_docs["b"]][0])) in pairs
    assert all(by_doc[seeded_docs["c"]][0] not in p for p in pairs)


async def test_document_detail_endpoint(seeded_docs):
    async with await dash_client() as client:
        resp = await client.get(f"/dash/api/documents/{seeded_docs['a']}")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["title"] == "Alfa"
    assert doc["collection"] == "grafos"
    assert len(doc["chunks"]) == 1 and doc["chunks"][0]["chunk_index"] == 0
    assert doc["versions"][0]["version"] == doc["version"]

    async with await dash_client() as client:
        resp = await client.get(f"/dash/api/documents/{uuid.uuid4()}")
    assert resp.status_code == 404
