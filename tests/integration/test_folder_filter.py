"""Filtro por pasta (plan-web-23): grafo e busca restritos à pasta + subpastas; árvore com contagens."""

import pytest

from mcp_rag_api import db
from mcp_rag_api.core import documents, folders, graph, search
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import NotFound

COLLECTION = "ff-filtro"


@pytest.fixture(scope="module")
async def tree():
    await documents.create_collection(ADMIN, COLLECTION)

    async def note(title: str, text: str, path: str | None) -> str:
        r = await documents.add_document(ADMIN, COLLECTION, title, text, force=True)
        if path:
            await folders.place_document_at_path(ADMIN, r["document_id"], path)
        return r["document_id"]

    ids = {
        "raiz": await note("Na raiz", "orcamento anual planilha custos", None),
        "a": await note("Em A", "reuniao conselho ata pauta orcamento", "Setor A"),
        "a1": await note("Em A/Sub", "ata reuniao pauta conselho votacao", "Setor A/Sub"),
        "a2": await note("Em A/Sub 2", "pauta conselho ata reuniao", "Setor A/Sub"),
        "b": await note("Em B", "contrato fornecedor licitacao pagamento", "Setor B"),
    }
    by_path = {f["path"]: f["id"] for f in await folders.list_folders(ADMIN, COLLECTION)}
    return ids, by_path


async def test_graph_folder_includes_subfolders_only(tree):
    ids, by_path = tree
    g = await graph.build_graph("documents", COLLECTION, 0.0, 5, folder_id=by_path["Setor A"])
    assert {n["id"] for n in g["nodes"]} == {ids["a"], ids["a1"], ids["a2"]}
    sub = await graph.build_graph("documents", COLLECTION, 0.0, 5, folder_id=by_path["Setor A/Sub"])
    assert {n["id"] for n in sub["nodes"]} == {ids["a1"], ids["a2"]}
    # sem pasta: a coleção inteira — e o cache não mistura as duas consultas
    full = await graph.build_graph("documents", COLLECTION, 0.0, 5)
    assert set(ids.values()) <= {n["id"] for n in full["nodes"]}
    chunks = await graph.build_graph("chunks", COLLECTION, 0.0, 3, folder_id=by_path["Setor B"])
    assert {n["document_id"] for n in chunks["nodes"]} == {ids["b"]}


async def test_search_folder_filter(tree):
    ids, by_path = tree
    async with db.pool().acquire() as conn:
        subtree = await folders.folder_subtree_ids(conn, ADMIN, by_path["Setor A"])
    hits = await search.search_knowledge(ADMIN, "ata reuniao conselho", [COLLECTION], top_k=10, folder_ids=subtree)
    assert hits and {h["document_id"] for h in hits} <= {ids["a"], ids["a1"], ids["a2"]}
    everywhere = await search.search_knowledge(ADMIN, "orcamento", [COLLECTION], top_k=10)
    assert ids["raiz"] in {h["document_id"] for h in everywhere}


async def test_location_tree_counts(tree):
    _, by_path = tree
    data = await folders.location_tree(ADMIN)
    mine = {f["path"]: f for f in data["folders"] if f["collection"] == COLLECTION}
    assert (mine["Setor A"]["documents"], mine["Setor A"]["total"]) == (1, 3)
    assert (mine["Setor A/Sub"]["documents"], mine["Setor A/Sub"]["total"]) == (2, 2)
    assert mine["Setor B"]["total"] == 1
    assert {"name": COLLECTION, "documents": 5} in data["collections"]


async def test_unknown_folder_is_not_found(tree):
    with pytest.raises(NotFound):
        await graph.build_graph("documents", COLLECTION, 0.0, 5, folder_id="00000000-0000-0000-0000-000000000000")
