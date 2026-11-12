"""Busca em camadas e documento por partes (plan-web-33) contra o banco real."""

import pytest

from mcp_rag_api import mcp_server
from mcp_rag_api.config import get_settings
from mcp_rag_api.core import documents, folders, lean
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN

COLLECTION = "lean-w33"
LONG = "\n\n".join(f"## Parte {i}\n\n" + "reuniao conselho orcamento ata " * 120 for i in range(4))


@pytest.fixture(scope="module")
async def docs():
    await documents.create_collection(ADMIN, COLLECTION)
    big = await documents.add_document(ADMIN, COLLECTION, "Ata longa", LONG, force=True)
    await folders.place_document_at_path(ADMIN, big["document_id"], "Atas/2026")
    small = await documents.add_document(
        ADMIN, COLLECTION, "Nota curta", "orcamento do conselho aprovado em reuniao", force=True
    )
    return big["document_id"], small["document_id"]


async def test_snippet_one_result_per_document(docs):
    big, small = docs
    out = await lean.search_lean(ADMIN, "reuniao conselho orcamento", [COLLECTION], top_k=5, detail="snippet")
    ids = [r["document_id"] for r in out["results"]]
    assert sorted(ids) == sorted([big, small]) and len(ids) == len(set(ids))
    by_id = {r["document_id"]: r for r in out["results"]}
    assert by_id[big]["folder_path"] == "Atas/2026" and by_id[big]["more_chunks"] >= 1
    assert "folder_path" not in by_id[small]  # raiz: null sai da resposta
    assert len(by_id[big]["snippet"].split()) <= get_settings().search_snippet_words + 2
    assert not out["truncated"] and out["tokens_estimate"] > 0


async def test_snippet_is_much_smaller_than_full(docs):
    full = await lean.search_lean(ADMIN, "reuniao conselho orcamento", [COLLECTION], top_k=5, detail="full")
    snip = await lean.search_lean(ADMIN, "reuniao conselho orcamento", [COLLECTION], top_k=5, detail="snippet")
    assert snip["tokens_estimate"] < full["tokens_estimate"] / 2


async def test_max_tokens_cuts_list_but_never_empty(docs):
    out = await lean.search_lean(ADMIN, "reuniao conselho orcamento", [COLLECTION], detail="full", max_tokens=30)
    assert out["truncated"] and len(out["results"]) == 1


async def test_mcp_get_document_big_returns_outline_then_section(docs, monkeypatch):
    big, _ = docs
    monkeypatch.setenv("DOC_FULL_MAX_TOKENS", "500")
    get_settings.cache_clear()
    try:
        overview = await mcp_server.get_document(_ctx(), big)
        assert "content" not in overview and [h["title"] for h in overview["outline"]][0] == "Parte 0"
        part = await mcp_server.get_document(_ctx(), big, section="parte 2")
        assert part["content"].startswith("## Parte 2") and "Parte 3" not in part["content"]
        assert part["folder_path"] == "Atas/2026"
    finally:
        get_settings.cache_clear()


async def test_mcp_search_default_is_snippet_and_full_switch(docs, monkeypatch):
    out = await mcp_server.search_knowledge(_ctx(), "reuniao conselho", [COLLECTION])
    assert "snippet" in out["results"][0]
    monkeypatch.setenv("SEARCH_DEFAULT_DETAIL", "full")
    get_settings.cache_clear()
    try:
        out = await mcp_server.search_knowledge(_ctx(), "reuniao conselho", [COLLECTION])
        assert "content" in out["results"][0]
    finally:
        get_settings.cache_clear()


def _ctx():
    from types import SimpleNamespace

    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(scope={"kb.principal": ADMIN})))
