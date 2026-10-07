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

    # memória, sessões e tarefas são escrita: exigem o escopo write
    with pytest.raises(PermissionDenied):
        await memory.remember(agent, "x")
    with pytest.raises(PermissionDenied):
        await memory.forget(agent, 1)
    with pytest.raises(PermissionDenied):
        await memory.upsert_task(agent, title="x")
    with pytest.raises(PermissionDenied):
        await memory.save_session(agent, "x")
    # leitura segue liberada com read
    assert (await memory.recall(agent, "ACME")) == []

    # ganha o escopo write: o fluxo de memória segue normalmente
    await agents.update_agent(ADMIN, "suporte", "habilitar escrita de memória", scopes=["read", "write"])
    agent = await principal_for(created["api_key"])
    assert agent.has("write")

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


async def test_memory_merge_preserves_expiration():
    """Merge de memória sem expires_in_days não pode zerar a expiração existente."""
    created = await agents.create_agent(ADMIN, slug="memoria-exp", name="M", system_prompt="x")
    agent = await principal_for(created["api_key"])

    m1 = await memory.remember(agent, "Senha do VPN rotaciona a cada 90 dias", expires_in_days=30)
    assert m1["action"] == "created"
    # regrava sem expires_in_days: o merge deve manter a expiração já existente
    m2 = await memory.remember(agent, "Senha do VPN rotaciona a cada 90 dias.", kind="procedure")
    assert m2["action"] == "updated_existing" and m2["memory_id"] == m1["memory_id"]

    row = await db.pool().fetchrow("SELECT expires_at FROM agent_memories WHERE id = $1", m1["memory_id"])
    assert row["expires_at"] is not None


P_DUP_1 = (
    "O cliente pode pedir reembolso em ate sete dias apos a compra pelo portal "
    "com o numero do pedido e o motivo do pedido deve ser informado no formulario do portal."
)
P_DUP_2 = (
    "O suporte responde em ate vinte e quatro horas uteis e o cliente recebe a confirmacao "
    "por e mail automaticamente quando o chamado e criado ou atualizado pela equipe de atendimento."
)
P_DUP_3 = (
    "Trocas de produto com defeito sao gratuitas nos primeiros trinta dias e o cliente "
    "deve guardar a nota fiscal para agilizar o atendimento na loja ou pelo site oficial."
)
P_DUP_OTHER = (
    "A paisagem da serra catarinense impressiona visitantes durante o inverno quando a "
    "geada cobre as hortensias e as estradas de terra ficam impraticaveis para carros baixos."
)


async def test_duplicate_detection_uses_document_centroid(monkeypatch):
    """Primeiro chunk diferente, resto duplicado: o centroide do documento pega a duplicata.

    Com o FakeProvider a similaridade do chunk 0 fica ~0,33 (abaixo de qualquer limiar
    razoável) enquanto a do centroide fica ~0,83; o limiar 0,6 isola a métrica nova —
    pela métrica antiga (chunk 0) este documento não seria detectado.
    """
    from mcp_rag_api.config import get_settings

    monkeypatch.setenv("DUPLICATE_THRESHOLD", "0.6")
    get_settings.cache_clear()
    try:
        await documents.create_collection(ADMIN, "dup-centroide")
        base_content = "\n\n".join([P_DUP_1, P_DUP_2, P_DUP_3])
        near_content = "\n\n".join([P_DUP_OTHER, P_DUP_2, P_DUP_3])
        base = await documents.add_document(ADMIN, "dup-centroide", "Política de atendimento", base_content)
        assert base["created"] is True

        near = await documents.add_document(ADMIN, "dup-centroide", "Manual do atendimento", near_content)
        assert near["created"] is False
        assert near["similar_document"]["document_id"] == base["document_id"]

        # controle: conteúdo realmente diferente não é marcado como duplicata
        other = await documents.add_document(ADMIN, "dup-centroide", "Turismo", P_DUP_OTHER * 3)
        assert other["created"] is True
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
