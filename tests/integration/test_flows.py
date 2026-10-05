import pytest

from mcp_rag_api import db
from mcp_rag_api.core import agents, documents, memory, search
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import KBError, PermissionDenied, resolve_key

MANUAL = (
    "Política de reembolso: o cliente pode pedir reembolso em até 7 dias após a compra. "
    "O pedido deve ser feito pelo portal, com o número do pedido."
)


async def principal_for(api_key: str):
    async with db.pool().acquire() as conn:
        return await resolve_key(conn, api_key)


async def test_documents_search_duplicates_and_versions():
    await documents.create_collection(ADMIN, "manuais", "Manuais internos")
    created = await documents.add_document(ADMIN, "manuais", "Reembolso", MANUAL, tags=["financeiro"])
    assert created["created"] is True
    doc_id = created["document_id"]

    dup = await documents.add_document(ADMIN, "manuais", "Reembolso (cópia)", MANUAL)
    assert dup["created"] is False and dup["similar_document"]["document_id"] == doc_id

    hits = await search.search_knowledge(ADMIN, "prazo para pedir reembolso")
    assert hits and hits[0]["document_id"] == doc_id

    no_change = await documents.update_document(ADMIN, doc_id, "nada", content=MANUAL)
    assert no_change["updated"] is False

    upd = await documents.update_document(ADMIN, doc_id, "prazo mudou", content=MANUAL.replace("7", "30"))
    assert upd == {"updated": True, "document_id": doc_id, "version": 2, "reindexed": True}
    history = await documents.document_history(ADMIN, doc_id)
    assert [h["version"] for h in history] == [2, 1]

    ext = await documents.upsert_document(ADMIN, "manuais", "crm-1", "Contato", "Telefone do suporte: ramal 200.")
    assert ext["created"] is True
    ext2 = await documents.upsert_document(ADMIN, "manuais", "crm-1", "Contato", "Telefone do suporte: ramal 300.")
    assert ext2["updated"] is True and ext2["version"] == 2

    await documents.archive_document(ADMIN, doc_id, "teste")
    assert all(h["document_id"] != doc_id for h in await search.search_knowledge(ADMIN, "reembolso"))


async def test_agent_lifecycle_via_services():
    await documents.create_collection(ADMIN, "vendas")
    created = await agents.create_agent(
        ADMIN,
        slug="suporte",
        name="Suporte pós-venda",
        system_prompt="Você é o suporte. Tom cordial. Nunca prometa reembolso.",
        allowed_collections=["manuais"],
        scopes=["read"],
    )
    assert created["agent"]["version"] == 1
    assert created["agent"]["config"]["auto_apply_updates"] is False
    assert "claude mcp add" in created["connect_command"]
    agent = await principal_for(created["api_key"])
    assert agent.agent_slug == "suporte" and not agent.has("write")

    # isolamento: coleção não permitida e escrita sem escopo
    with pytest.raises(PermissionDenied):
        await search.search_knowledge(agent, "x", collections=["vendas"])
    with pytest.raises(PermissionDenied):
        await documents.add_document(agent, "manuais", "t", "c")
    with pytest.raises(PermissionDenied):
        await agents.create_agent(agent, "outro", "Outro", "x")

    # memória: cria, deduplica, lembra
    m1 = await memory.remember(agent, "Cliente ACME prefere contato por WhatsApp", kind="preference", importance=4)
    assert m1["action"] == "created"
    m2 = await memory.remember(agent, "Cliente ACME prefere contato por WhatsApp.", kind="preference")
    assert m2["action"] == "updated_existing"
    recalled = await memory.recall(agent, "como falar com a ACME")
    assert recalled[0]["id"] == m1["memory_id"]

    task = await memory.upsert_task(agent, title="Enviar proposta ACME")
    await memory.save_session(agent, "Atendi a ACME.", next_steps="Enviar proposta")

    ctx = await memory.load_agent(agent)
    assert ctx["agent"]["slug"] == "suporte"
    assert ctx["last_session"]["next_steps"] == "Enviar proposta"
    assert [t["id"] for t in ctx["open_tasks"]] == [task["id"]]
    assert ctx["memories"][0]["kind"] == "preference"

    # agente não acessa outro agente
    await agents.create_agent(ADMIN, slug="sdr", name="SDR", system_prompt="Você é SDR.")
    with pytest.raises(PermissionDenied):
        await memory.load_agent(agent, "sdr")


async def test_proposals_autonomy_and_protected_fields():
    created = await agents.create_agent(ADMIN, slug="financeiro", name="Fin", system_prompt="v1")
    agent = await principal_for(created["api_key"])

    # autonomia desligada: proposta fica pendente; campo protegido é ignorado
    prop = await agents.propose_agent_update(
        agent, "melhorar", system_prompt="v2", config={"auto_apply_updates": True, "tom": "formal"}
    )
    assert prop["status"] == "proposed" and prop["ignored_fields"] == ["auto_apply_updates"]
    with pytest.raises(PermissionDenied):
        await agents.review_agent_update(agent, prop["proposal_id"], True)
    with pytest.raises(PermissionDenied):
        await agents.set_agent_autonomy(agent, "financeiro", True)

    reviewed = await agents.review_agent_update(ADMIN, prop["proposal_id"], True)
    assert reviewed["version"] == 2
    profile = (await agents.get_agent(ADMIN, "financeiro"))["agent"]
    assert profile["system_prompt"] == "v2"
    assert profile["config"] == {"auto_apply_updates": False, "tom": "formal"}

    # só proposta com campo protegido: nada a mudar
    with pytest.raises(KBError):
        await agents.propose_agent_update(agent, "tentar", config={"auto_apply_updates": True})

    # usuário liga a autonomia pelo "chat": proposta aplica na hora
    await agents.set_agent_autonomy(ADMIN, "financeiro", True)
    auto = await agents.propose_agent_update(agent, "ajuste", system_prompt="v3")
    assert auto["status"] == "applied"
    assert (await agents.get_agent(ADMIN, "financeiro"))["agent"]["system_prompt"] == "v3"

    # restaurar versão 2
    restored = await agents.restore_agent_version(ADMIN, "financeiro", 2)
    assert restored["system_prompt"] == "v2"
    assert restored["config"]["auto_apply_updates"] is False

    # arquivar revoga chaves
    await agents.archive_agent(ADMIN, "financeiro", "teste")
    with pytest.raises(PermissionDenied):
        await principal_for(created["api_key"])


async def test_reindex_recomputes_chunks_and_centroids():
    from mcp_rag_api.core.reindex import reindex_all

    await documents.create_collection(ADMIN, "reindexacao")
    doc = await documents.add_document(ADMIN, "reindexacao", "Reindex", "zulu yankee xray whiskey victor", force=True)
    doc_id = doc["document_id"]
    # simula vetores de outro modelo: zera o centroide e apaga os chunks
    await db.pool().execute("UPDATE documents SET centroid = NULL WHERE id = $1::uuid", doc_id)
    await db.pool().execute("DELETE FROM chunks WHERE document_id = $1::uuid", doc_id)

    lines = []
    result = await reindex_all(progress=lines.append)
    assert result["documents"] >= 1 and any("Reindex" in line for line in lines)
    row = await db.pool().fetchrow(
        "SELECT d.centroid IS NOT NULL AS has_centroid, "
        "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS n "
        "FROM documents d WHERE d.id = $1::uuid",
        doc_id,
    )
    assert row["has_centroid"] and row["n"] >= 1
