"""Links explícitos (plan-web-03): [[wikilinks]], pendentes, manuais, backlinks, get_related, relink.

Coleções com prefixo "lk-" (o banco é compartilhado entre os módulos de integração).
"""

import pytest

from mcp_rag_api import db
from mcp_rag_api.core import documents, links
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import KBError, PermissionDenied, Principal


async def _doc(collection: str, title: str, content: str) -> str:
    created = await documents.add_document(ADMIN, collection, title, content, force=True)
    return created["document_id"]


@pytest.fixture(scope="module")
async def base():
    await documents.create_collection(ADMIN, "lk-produto")
    await documents.create_collection(ADMIN, "lk-financeiro")
    auth = await _doc("lk-produto", "Autenticação", "chaves bearer com escopos read write admin")
    reg = await _doc("lk-produto", "Registro de agentes", "perfil memórias e tarefas. Usa [[autenticacao]].")
    fin = await _doc("lk-financeiro", "Orçamento", "planilha de custos")
    return {"auth": auth, "reg": reg, "fin": fin}


async def test_wikilink_resolves_ignoring_accents_and_creates_backlink(base):
    out = await links.get_links(ADMIN, base["reg"])
    assert [(lk["title"], lk["kind"], lk["pending"]) for lk in out["links"]] == [("Autenticação", "wikilink", False)]
    back = await links.get_links(ADMIN, base["auth"])
    assert [b["title"] for b in back["backlinks"]] == ["Registro de agentes"]


async def test_pending_link_resolves_when_target_is_created(base):
    src = await _doc("lk-produto", "Roadmap", "próximo passo: [[Plano Comercial]] e [[Autenticação]]")
    out = await links.get_links(ADMIN, src)
    pending = [lk for lk in out["links"] if lk["pending"]]
    assert [p["target_title"] for p in pending] == ["Plano Comercial"]

    plano = await _doc("lk-produto", "plano comercial", "metas do trimestre")
    out = await links.get_links(ADMIN, src)
    assert not any(lk["pending"] for lk in out["links"])
    assert any(lk["document_id"] == plano for lk in out["links"])


async def test_update_resyncs_wikilinks_and_rename_resolves_pending(base):
    src = await _doc("lk-produto", "Notas soltas", "fala de [[Autenticação]] e de [[Glossário]]")
    await documents.update_document(ADMIN, src, "tira o link de auth", content="só [[Glossário]] agora")
    out = await links.get_links(ADMIN, src)
    assert [(lk["target_title"], lk["pending"]) for lk in out["links"]] == [("Glossário", True)]

    # um documento existente é renomeado para o título citado -> o pendente se resolve
    other = await _doc("lk-produto", "Termos", "definições")
    await documents.update_document(ADMIN, other, "renomeia", title="Glossário")
    out = await links.get_links(ADMIN, src)
    assert [(lk["document_id"], lk["pending"]) for lk in out["links"]] == [(other, False)]


async def test_self_reference_is_ignored(base):
    doc = await _doc("lk-produto", "Autorreferência", "este é o [[Autorreferência]] mesmo")
    assert (await links.get_links(ADMIN, doc))["links"] == []


async def test_manual_link_idempotent_unlink_and_wikilink_protection(base):
    first = await links.link_documents(ADMIN, base["auth"], base["fin"], note="custo de infra de auth")
    again = await links.link_documents(ADMIN, base["auth"], base["fin"])
    assert first["created"] and not again["created"] and first["link_id"] == again["link_id"]

    out = await links.get_links(ADMIN, base["auth"])
    manual = [lk for lk in out["links"] if lk["kind"] == "manual"]
    assert manual[0]["note"] == "custo de infra de auth"

    wiki = (await links.get_links(ADMIN, base["reg"]))["links"][0]
    with pytest.raises(KBError, match="wikilink"):
        await links.unlink_documents(ADMIN, wiki["id"])
    with pytest.raises(KBError):
        await links.link_documents(ADMIN, base["auth"], base["auth"])

    await links.unlink_documents(ADMIN, first["link_id"])
    out = await links.get_links(ADMIN, base["auth"])
    assert not [lk for lk in out["links"] if lk["kind"] == "manual"]


async def test_permissions_respect_scopes_and_collections(base):
    reader = Principal(actor="leitor", scopes=frozenset({"read"}))
    with pytest.raises(PermissionDenied):
        await links.link_documents(reader, base["reg"], base["auth"])

    await links.link_documents(ADMIN, base["fin"], base["auth"], note="orçamento cita auth")
    only_produto = Principal(actor="prod", scopes=frozenset({"read"}), allowed_collections=("lk-produto",))
    back = await links.get_links(only_produto, base["auth"])
    assert all(b["collection"] == "lk-produto" for b in back["backlinks"])  # Orçamento (financeiro) some
    with pytest.raises(PermissionDenied):
        await links.get_links(only_produto, base["fin"])


async def test_get_related_includes_semantic_neighbors(base):
    rel = await links.get_related(ADMIN, base["reg"], k=3)
    assert set(rel) == {"document", "links", "backlinks", "semantic"}
    assert rel["semantic"] and all(s["document_id"] != base["reg"] for s in rel["semantic"])
    assert len(rel["semantic"]) <= 3


async def test_archived_target_disappears_and_relink_backfills(base):
    gone = await _doc("lk-produto", "Rascunho velho", "texto")
    src = await _doc("lk-produto", "Índice", "ver [[Rascunho velho]]")
    await documents.archive_document(ADMIN, gone, "obsoleto")
    assert (await links.get_links(ADMIN, src))["links"] == []

    await db.pool().execute("DELETE FROM document_links WHERE source_id = $1::uuid", base["reg"])
    result = await links.relink_all()
    assert result["documents"] >= 3 and result["wikilinks"] >= 1
    assert (await links.get_links(ADMIN, base["reg"]))["links"][0]["title"] == "Autenticação"


async def test_graph_marks_explicit_links_regardless_of_threshold(base):
    from mcp_rag_api.core import graph

    g = await graph.build_graph("documents", "lk-produto", 0.99, 3)  # threshold alto: sem semânticas
    link_edges = [e for e in g["edges"] if e["kind"] == "link"]
    pair = {base["reg"], base["auth"]}
    assert any({e["source"], e["target"]} == pair and "wikilink" in e["link_kinds"] for e in link_edges)
    assert all(e["kind"] == "link" for e in g["edges"])

    loose = await graph.build_graph("documents", "lk-produto", 0.0, 3)
    kinds = {e["kind"] for e in loose["edges"]}
    assert kinds == {"link", "semantic"}
    # o par ligado aparece uma vez só (link vence a semântica)
    assert sum(1 for e in loose["edges"] if {e["source"], e["target"]} == pair) == 1

    chunks = await graph.build_graph("chunks", "lk-produto", 0.0, 3)
    assert all(e["kind"] == "semantic" for e in chunks["edges"])
