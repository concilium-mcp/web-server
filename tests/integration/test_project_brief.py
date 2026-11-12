"""Resumo do projeto no início da sessão (plan-web-34)."""

import uuid
from types import SimpleNamespace

import pytest

from mcp_rag_api import mcp_server
from mcp_rag_api.config import get_settings
from mcp_rag_api.core import agents, brief, documents, folders, memory
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import KBError, PermissionDenied, Principal

COLLECTION = "brief-w34"
OTHER = "brief-w34-outra"


@pytest.fixture(scope="module")
async def setup():
    await documents.create_collection(ADMIN, COLLECTION)
    await documents.create_collection(ADMIN, OTHER)
    note = await documents.add_document(ADMIN, COLLECTION, "Resumo", "Projeto de teste. Stack: Python.", force=True)
    deep = await documents.add_document(ADMIN, COLLECTION, "Plano 1", "detalhes do plano", force=True)
    await folders.place_document_at_path(ADMIN, deep["document_id"], "Planos/2026")
    foreign = await documents.add_document(ADMIN, OTHER, "De outra", "nao pertence", force=True)
    return note["document_id"], foreign["document_id"]


async def test_without_brief_lists_folders_and_hint(setup):
    data = await brief.get_brief(ADMIN, COLLECTION)
    assert data["brief_document_id"] is None
    assert "ainda não tem nota de resumo" in data["text"]
    assert "- Planos/2026: 1 nota" in data["text"]
    assert 'collections=["brief-w34"]' in data["text"]


async def test_set_brief_and_text(setup):
    note_id, foreign_id = setup
    with pytest.raises(KBError):
        await brief.set_brief(ADMIN, COLLECTION, foreign_id)  # nota de outra coleção
    await brief.set_brief(ADMIN, COLLECTION, note_id)
    data = await brief.get_brief(ADMIN, COLLECTION)
    assert data["brief_document_id"] == note_id and not data["truncated"]
    assert data["text"].startswith("# brief-w34 — resumo do projeto") and "Stack: Python." in data["text"]
    assert data["tokens_estimate"] < 300


async def test_brief_cut_at_ceiling(setup, monkeypatch):
    note_id, _ = setup
    await documents.update_document(ADMIN, note_id, "maior", content="palavra " * 2000)
    monkeypatch.setenv("BRIEF_MAX_TOKENS", "100")
    get_settings.cache_clear()
    try:
        data = await brief.get_brief(ADMIN, COLLECTION)
        assert data["truncated"] and "encurte a nota" in data["text"]
        assert data["tokens_estimate"] < 400
    finally:
        get_settings.cache_clear()
        await documents.update_document(ADMIN, note_id, "volta", content="Projeto de teste. Stack: Python.")


async def test_permissions(setup):
    reader_other = Principal(actor="key:x", scopes=frozenset({"read"}), allowed_collections=(OTHER,))
    with pytest.raises(PermissionDenied):
        await brief.get_brief(reader_other, COLLECTION)
    reader = Principal(actor="key:r", scopes=frozenset({"read"}))
    with pytest.raises(PermissionDenied):
        await brief.set_brief(reader, COLLECTION, None)


async def test_agent_session_and_tasks_and_load_agent(setup):
    note_id, _ = setup
    await brief.set_brief(ADMIN, COLLECTION, note_id)
    slug = f"brief-{uuid.uuid4().hex[:6]}"
    await agents.create_agent(ADMIN, slug, "Agente brief", "Você é um teste.", allowed_collections=[COLLECTION])
    await memory.save_session(ADMIN, "fechei o plano 1", "abrir MR", agent_slug=slug)
    await memory.upsert_task(ADMIN, title="revisar resumo", agent_slug=slug)
    data = await brief.get_brief(ADMIN, COLLECTION, slug)
    assert "fechei o plano 1" in data["text"] and "Próximos passos: abrir MR" in data["text"]
    assert "- [open] revisar resumo" in data["text"]
    # outro agente não lê a sessão deste
    stranger = Principal(actor="agent:x", scopes=frozenset({"read"}), agent_slug="x")
    with pytest.raises(KBError):
        await brief.get_brief(stranger, COLLECTION, slug)
    # load_agent do MCP traz o resumo do projeto do agente
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(scope={"kb.principal": ADMIN})))
    loaded = await mcp_server.load_agent(ctx, slug)
    assert loaded["project_brief"]["collection"] == COLLECTION
    assert "Stack: Python." in loaded["project_brief"]["text"]


async def test_archived_brief_counts_as_none(setup):
    _, _ = setup
    doc = await documents.add_document(ADMIN, COLLECTION, "Resumo temporário", "texto temporario unico", force=True)
    await brief.set_brief(ADMIN, COLLECTION, doc["document_id"])
    await documents.archive_document(ADMIN, doc["document_id"], "teste")
    data = await brief.get_brief(ADMIN, COLLECTION)
    assert data["brief_document_id"] is None
    ids = await brief.brief_document_ids([COLLECTION])
    assert ids.get(COLLECTION) == doc["document_id"]  # a marca fica, mas não é usada (nota arquivada)
