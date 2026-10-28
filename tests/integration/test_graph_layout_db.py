"""Layout do grafo no servidor (plan-web-26): posições e grupos no /graph, gravados no Postgres e
atualizados de forma incremental quando a base muda pouco."""

import pytest

from mcp_rag_api import db
from mcp_rag_api.core import documents, graph, graph_layout
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN

COLLECTION = "gl-layout"
TOPICS = [
    "conselho saude hospital posto medico vacina",
    "saude vacina posto conselho atendimento medico",
    "orcamento credito suplementar receita despesa fundo",
    "credito orcamento despesa receita suplementar anual",
    "alvara obra construcao licenca reforma habite",
    "obra alvara licenca construcao reforma projeto",
]


@pytest.fixture(scope="module")
async def docs():
    await documents.create_collection(ADMIN, COLLECTION)
    ids = []
    for i, text in enumerate(TOPICS * 2):
        r = await documents.add_document(ADMIN, COLLECTION, f"Doc {i} {text.split()[0]}", text, force=True)
        ids.append(r["document_id"])
    graph.clear_cache()
    graph_layout.clear_cache()
    return ids


async def test_nodes_come_with_positions_and_groups(docs):
    data = await graph.build_graph("documents", COLLECTION, 0.0, 3)
    assert data["view"] == "all" and data["total_nodes"] == len(docs)
    for n in data["nodes"]:
        assert isinstance(n["x"], float) and isinstance(n["y"], float) and isinstance(n["group"], int)


async def test_groups_view_and_group_filter(docs):
    groups = await graph.build_graph("documents", COLLECTION, 0.0, 3, view="groups")
    assert groups["view"] == "groups" and groups["total_nodes"] == len(docs)
    assert sum(g["size"] for g in groups["nodes"]) == len(docs)
    assert all(g["id"].startswith("group:") and g["title"] for g in groups["nodes"])
    assert all(e["kind"] == "group" and e["weight"] >= 1 for e in groups["edges"])

    first = groups["nodes"][0]
    one = await graph.build_graph("documents", COLLECTION, 0.0, 3, group=first["group"])
    assert one["view"] == "group" and one["group"]["id"] == first["group"]
    assert len(one["nodes"]) == first["size"]
    assert {n["group"] for n in one["nodes"]} == {first["group"]}
    ids = {n["id"] for n in one["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in one["edges"])


async def test_layout_is_stored_and_reused_without_recomputing(docs, monkeypatch):
    await graph.build_graph("documents", COLLECTION, 0.0, 3)
    row = await db.pool().fetchrow("SELECT stamp FROM graph_layouts WHERE level = 'documents'")
    assert row is not None

    # sem memória (ex.: restart): vem do Postgres, sem calcular de novo
    graph_layout.clear_cache()

    def boom(*_a, **_k):
        raise AssertionError("não deveria recalcular")

    monkeypatch.setattr(graph_layout, "compute", boom)
    again = await graph.build_graph("documents", COLLECTION, 0.0, 3)
    assert all("x" in n for n in again["nodes"])


async def test_new_document_is_placed_without_moving_the_others(docs, monkeypatch):
    before = {n["id"]: (n["x"], n["y"]) for n in (await graph.build_graph("documents", COLLECTION, 0.0, 3))["nodes"]}
    monkeypatch.setattr(graph_layout, "compute", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("completo")))
    new = await documents.add_document(ADMIN, COLLECTION, "Doc novo saude", TOPICS[0] + " campanha", force=True)
    after = {n["id"]: (n["x"], n["y"]) for n in (await graph.build_graph("documents", COLLECTION, 0.0, 3))["nodes"]}
    assert new["document_id"] in after
    assert all(after[doc_id] == pos for doc_id, pos in before.items())
