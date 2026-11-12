"""API REST: espelho das tools MCP para integrações que não falam MCP."""

import asyncio
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from . import db
from .config import get_settings
from .core import (
    agents,
    boards,
    brief,
    documents,
    file_import,
    folders,
    lean,
    links,
    memory,
    search,
    token_meter,
    usage,
)
from .security import DEV_PRINCIPAL, PermissionDenied, Principal, bearer_token, resolve_bearer

router = APIRouter()


async def principal(authorization: str | None = Header(default=None)) -> Principal:
    token = bearer_token(authorization)
    if token:
        async with db.pool().acquire() as conn:
            return await resolve_bearer(conn, token)
    if get_settings().kb_auth_disabled:
        return DEV_PRINCIPAL
    raise PermissionDenied("Autenticação necessária: Authorization: Bearer <chave>.")


Auth = Depends(principal)


# ---------------------------------------------------------------- modelos


class CollectionIn(BaseModel):
    name: str
    description: str | None = None


class SearchIn(BaseModel):
    query: str
    collections: list[str] | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    top_k: int = 5
    # sem detail: lista de trechos inteiros (formato de sempre); com detail: {results, truncated, ...}
    detail: Literal["snippet", "full"] | None = None
    max_tokens: int | None = Field(default=None, ge=1)


class DocumentIn(BaseModel):
    collection: str
    title: str
    content: str
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: str | None = None
    external_id: str | None = None
    force: bool = False
    folder_path: str | None = None  # "A/B": pasta dentro da coleção (criada se faltar); None = raiz


class DocumentPatch(BaseModel):
    change_note: str
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: str | None = None
    base_version: int | None = None  # trava otimista opcional: conflito devolve 409 + current_version


class TextEditIn(BaseModel):
    old_text: str
    new_text: str


class DocumentEdit(BaseModel):
    change_note: str
    edits: list[TextEditIn] | None = None
    append: str | None = None
    base_version: int | None = None


class LinkIn(BaseModel):
    target_id: str
    note: str | None = None


class DocumentUpsert(BaseModel):
    collection: str
    external_id: str
    title: str
    content: str
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: str | None = None
    change_note: str = "sincronização (upsert)"
    folder_path: str | None = None


class DocumentMove(BaseModel):
    collection: str
    folder_path: str | None = None


class FolderPathIn(BaseModel):
    collection: str
    path: str


class AgentIn(BaseModel):
    slug: str
    name: str
    system_prompt: str
    description: str | None = None
    config: dict[str, Any] | None = None
    allowed_collections: list[str] | None = None
    scopes: list[str] | None = None


class AgentPatch(BaseModel):
    change_note: str
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    config: dict[str, Any] | None = None
    allowed_collections: list[str] | None = None
    scopes: list[str] | None = None


class AutonomyIn(BaseModel):
    auto_apply_updates: bool
    note: str | None = None


class CloneIn(BaseModel):
    new_slug: str
    name: str
    overrides: dict[str, Any] | None = None


class ReviewIn(BaseModel):
    approve: bool
    note: str | None = None


class MemoryIn(BaseModel):
    content: str
    kind: str = "fact"
    importance: int = Field(default=3, ge=1, le=5)
    shared: bool = False
    expires_in_days: int | None = None
    source: str | None = None


class SessionIn(BaseModel):
    summary: str
    next_steps: str | None = None
    started_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class TaskIn(BaseModel):
    task_id: int | None = None
    title: str | None = None
    details: str | None = None
    status: str | None = None
    due_at: datetime | None = None


# ---------------------------------------------------------------- base de conhecimento


@router.get("/health")
async def health() -> dict:
    await db.pool().fetchval("SELECT 1")
    return {"status": "ok"}


@router.get("/collections")
async def list_collections(p: Principal = Auth) -> list[dict]:
    return await documents.list_collections(p)


@router.post("/collections")
async def create_collection(body: CollectionIn, p: Principal = Auth) -> dict:
    return await documents.create_collection(p, body.name, body.description)


class CollectionRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.patch("/collections/{name}")
async def rename_collection(name: str, body: CollectionRenameIn, p: Principal = Auth) -> dict:
    """Renomeia a coleção (escopo admin); leva o nome novo para as listas de acesso restrito."""
    return await folders.rename_collection(p, name, body.name)


@router.delete("/collections/{name}")
async def delete_collection(name: str, p: Principal = Auth) -> dict:
    """Exclui a coleção sem notas ativas (escopo admin) — irreversível."""
    return await folders.delete_collection(p, name)


@router.get("/folders")
async def list_folders(collection: str | None = None, p: Principal = Auth) -> list[dict]:
    return await folders.list_folders(p, collection)


@router.post("/folders")
async def create_folder(body: FolderPathIn, p: Principal = Auth) -> dict:
    return await folders.ensure_folder_path(p, body.collection, body.path)


@router.post("/search")
async def search_knowledge(body: SearchIn, p: Principal = Auth) -> list[dict] | dict:
    if body.detail is None and body.max_tokens is None:
        rows = await search.search_knowledge(p, body.query, body.collections, body.tags, body.metadata, body.top_k)
        token_meter.record_documents("search_knowledge", p, token_meter.served_by_document(rows))
        return rows
    result = await lean.search_lean(
        p, body.query, body.collections, body.tags, body.metadata, body.top_k, body.detail or "full", body.max_tokens
    )
    token_meter.record_documents("search_knowledge", p, token_meter.served_by_document(result["results"]))
    return result


@router.get("/context/brief")
async def project_brief(
    collection: str,
    agent: str | None = None,
    repo: str | None = None,
    session: str | None = None,
    p: Principal = Auth,
) -> dict:
    """Resumo do projeto para o início da sessão (plugin do Claude Code, plan-web-34), com os cartões
    do agente (repo filtra pelo repositório; session marca os desta sessão) — plan-web-37a."""
    return await brief.get_brief(p, collection, agent, repo=repo, work_session=session)


@router.get("/documents")
async def list_documents(
    collection: str | None = None,
    tags: list[str] | None = Query(default=None),
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
    folder_path: str | None = None,
    recursive: bool = True,
    p: Principal = Auth,
) -> list[dict]:
    return await folders.list_documents_in_folder(p, collection, tags, status, limit, offset, folder_path, recursive)


@router.post("/documents")
async def add_document(body: DocumentIn, p: Principal = Auth) -> dict:
    data = body.model_dump()
    return await folders.add_document_in_folder(p, data.pop("folder_path"), **data)


@router.put("/documents/upsert")
async def upsert_document(body: DocumentUpsert, p: Principal = Auth) -> dict:
    data = body.model_dump()
    return await folders.upsert_document_in_folder(p, data.pop("folder_path"), **data)


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    collection: str = Form(...),
    folder_path: str | None = Form(None),
    p: Principal = Auth,
) -> dict:
    """PDF, Word (.docx), Markdown ou texto → nota vetorizada (plan-web-28).

    Re-enviar o mesmo arquivo para a mesma coleção/pasta atualiza a nota (nova versão).
    """
    p.require("write")
    p.require_collection(collection)
    name = file_import.safe_filename(file.filename or "")
    file_import.file_type_for(name)
    data = await file.read(file_import.MAX_FILE_BYTES + 1)
    if len(data) > file_import.MAX_FILE_BYTES:
        mb = file_import.MAX_FILE_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"'{name}' passa de {mb} MB.")
    engine, max_ocr_pages = file_import.ocr_options(rest=True)
    converted = await asyncio.to_thread(file_import.convert_file, name, data, engine, max_ocr_pages)
    return await file_import.import_file(p, collection, folder_path or None, converted)


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    outline: bool = False,
    section: str | None = None,
    offset: int = Query(default=0, ge=0),
    max_tokens: int | None = Query(default=None, ge=1),
    p: Principal = Auth,
) -> dict:
    doc = await folders.get_document_with_folder(p, document_id)
    # na REST o documento inteiro continua sendo o padrão (sem auto_outline): scripts contam com isso
    shaped = lean.shape_document(doc, outline=outline, section=section, offset=offset, max_tokens=max_tokens)
    token_meter.record_documents("get_document", p, token_meter.served_by_document([shaped]))
    return shaped


@router.post("/documents/{document_id}/move")
async def move_document(document_id: str, body: DocumentMove, p: Principal = Auth) -> dict:
    return await folders.move_document_to(p, document_id, body.collection, body.folder_path)


@router.patch("/documents/{document_id}")
async def update_document(document_id: str, body: DocumentPatch, p: Principal = Auth) -> dict:
    return await documents.update_document(p, document_id, **body.model_dump())


@router.post("/documents/{document_id}/edit")
async def edit_document(document_id: str, body: DocumentEdit, p: Principal = Auth) -> dict:
    return await documents.edit_document(p, document_id, **body.model_dump())


@router.delete("/documents/{document_id}")
async def archive_document(document_id: str, reason: str = "arquivado via API", p: Principal = Auth) -> dict:
    return await documents.archive_document(p, document_id, reason)


@router.get("/documents/{document_id}/versions")
async def document_history(document_id: str, p: Principal = Auth) -> list[dict]:
    return await documents.document_history(p, document_id)


@router.get("/documents/{document_id}/links")
async def document_links(document_id: str, p: Principal = Auth) -> dict:
    return await links.get_links(p, document_id)


@router.get("/documents/{document_id}/related")
async def related_documents(document_id: str, k: int = Query(default=5, ge=1, le=20), p: Principal = Auth) -> dict:
    return await links.get_related(p, document_id, k)


@router.post("/documents/{document_id}/links")
async def link_documents(document_id: str, body: LinkIn, p: Principal = Auth) -> dict:
    return await links.link_documents(p, document_id, body.target_id, body.note)


@router.delete("/links/{link_id}")
async def unlink_documents(link_id: int, p: Principal = Auth) -> dict:
    return await links.unlink_documents(p, link_id)


# ---------------------------------------------------------------- agentes


@router.get("/agents")
async def list_agents(include_archived: bool = False, p: Principal = Auth) -> list[dict]:
    return await agents.list_agents(p, include_archived)


@router.post("/agents")
async def create_agent(body: AgentIn, p: Principal = Auth) -> dict:
    return await agents.create_agent(p, **body.model_dump())


@router.get("/agents/proposals")
async def list_proposals(slug: str | None = None, p: Principal = Auth) -> list[dict]:
    return await agents.list_proposals(p, slug)


@router.post("/agents/proposals/{proposal_id}/review")
async def review_proposal(proposal_id: int, body: ReviewIn, p: Principal = Auth) -> dict:
    return await agents.review_agent_update(p, proposal_id, body.approve, body.note)


@router.get("/agents/{slug}")
async def get_agent(slug: str, p: Principal = Auth) -> dict:
    return await agents.get_agent(p, slug)


@router.patch("/agents/{slug}")
async def update_agent(slug: str, body: AgentPatch, p: Principal = Auth) -> dict:
    return await agents.update_agent(p, slug, **body.model_dump())


@router.put("/agents/{slug}/autonomy")
async def set_autonomy(slug: str, body: AutonomyIn, p: Principal = Auth) -> dict:
    return await agents.set_agent_autonomy(p, slug, body.auto_apply_updates, body.note)


@router.delete("/agents/{slug}")
async def archive_agent(slug: str, reason: str = "arquivado via API", p: Principal = Auth) -> dict:
    return await agents.archive_agent(p, slug, reason)


@router.post("/agents/{slug}/keys")
async def issue_key(slug: str, label: str | None = None, p: Principal = Auth) -> dict:
    return await agents.issue_agent_key(p, slug, label)


@router.delete("/keys/{key_prefix}")
async def revoke_agent_key(key_prefix: str, p: Principal = Auth) -> dict:
    """Revoga uma chave pelo prefixo (ex.: 'kb_sk_AbCdE'). Exige agents:manage (ou admin)."""
    return await agents.revoke_agent_key(p, key_prefix)


@router.post("/agents/{slug}/clone")
async def clone_agent(slug: str, body: CloneIn, p: Principal = Auth) -> dict:
    """Cria um agente novo copiando outro. Exige agents:manage (ou admin)."""
    return await agents.clone_agent(p, slug, body.new_slug, body.name, body.overrides)


@router.post("/agents/{slug}/restore/{version}")
async def restore_agent_version(slug: str, version: int, change_note: str | None = None, p: Principal = Auth) -> dict:
    """Volta o perfil do agente para uma versão anterior. Exige agents:manage (ou admin)."""
    return await agents.restore_agent_version(p, slug, version, change_note)


@router.get("/agents/{slug}/context")
async def agent_context(slug: str, p: Principal = Auth) -> dict:
    return await memory.load_agent(p, slug)


@router.get("/agents/{slug}/memories")
async def recall(slug: str, query: str, include_shared: bool = True, limit: int = 8, p: Principal = Auth) -> list[dict]:
    return await memory.recall(p, query, slug, include_shared, limit)


@router.post("/agents/{slug}/memories")
async def remember(slug: str, body: MemoryIn, p: Principal = Auth) -> dict:
    return await memory.remember(p, agent_slug=slug, **body.model_dump())


@router.delete("/agents/{slug}/memories/{memory_id}")
async def forget(slug: str, memory_id: int, replacement: str | None = None, p: Principal = Auth) -> dict:
    """Apaga uma memória; com `replacement`, corrige o conteúdo em vez de apagar."""
    return await memory.forget(p, memory_id, replacement, slug)


@router.get("/agents/{slug}/sessions")
async def list_sessions(slug: str, limit: int = 10, p: Principal = Auth) -> list[dict]:
    return await memory.list_sessions(p, slug, limit)


@router.post("/agents/{slug}/sessions")
async def save_session(slug: str, body: SessionIn, p: Principal = Auth) -> dict:
    return await memory.save_session(p, agent_slug=slug, **body.model_dump())


@router.get("/agents/{slug}/tasks")
async def list_tasks(slug: str, status: list[str] | None = Query(default=None), p: Principal = Auth) -> list[dict]:
    return await memory.list_tasks(p, status, slug)


@router.post("/agents/{slug}/tasks")
async def upsert_task(slug: str, body: TaskIn, p: Principal = Auth) -> dict:
    return await memory.upsert_task(p, agent_slug=slug, **body.model_dump())


# ---------------------------------------------------------------- quadros (plan-web-37a)
# Origem no log: cliente = User-Agent; sessão = cabeçalho X-Concilium-Session (ou "session" no corpo).


def board_origin(
    user_agent: str | None = Header(default=None), x_concilium_session: str | None = Header(default=None)
) -> boards.Origin:
    return boards.Origin.of(user_agent or "rest", x_concilium_session)


Origin = Depends(board_origin)


class BoardIn(BaseModel):
    collection: str
    name: str
    folder_path: str | None = None
    description: str | None = None
    columns: list[str | dict[str, Any]] | None = None
    owner: str | None = None


class BoardPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    owner: str | None = None
    folder_path: str | None = None
    archived: bool | None = None


class ColumnIn(BaseModel):
    name: str
    role: str | None = None
    position: int | None = None
    wip_limit: int | None = None


class ColumnPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    position: int | None = None
    wip_limit: int | None = None


class CardIn(BaseModel):
    title: str
    description: str | None = None
    checklist: list[str] | None = None
    labels: list[str] | None = None
    due: str | None = None
    column: str | None = None
    assignee: str | None = None
    repo: str | None = None


class CardPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    due: str | None = None
    labels: list[str] | None = None
    repo: str | None = None
    worktree: str | None = None
    branch: str | None = None
    mr_url: str | None = None
    ci_status: str | None = None
    column: str | None = None
    position: int | None = None
    assignee: str | None = None
    archived: bool | None = None
    session: str | None = None


class StartIn(BaseModel):
    repo: str | None = None
    force: bool = False
    session: str | None = None


class ItemsIn(BaseModel):
    items: list[str]
    session: str | None = None


class CheckIn(BaseModel):
    item_id: int | None = None
    text: str | None = None
    done: bool = True
    session: str | None = None


class CardLogIn(BaseModel):
    text: str
    kind: str = "note"
    session: str | None = None


class BlockIn(BaseModel):
    reason: str | None = None
    session: str | None = None


class ReleaseIn(BaseModel):
    next_step: str
    force: bool = False
    session: str | None = None


class SubmitIn(BaseModel):
    summary: str
    mr_url: str | None = None
    session: str | None = None


class FinishIn(BaseModel):
    summary: str
    append_to_document: str | None = None
    session: str | None = None


class CardDocumentIn(BaseModel):
    document_id: str
    unlink: bool = False


def _with_session(origin: boards.Origin, session: str | None) -> boards.Origin:
    return boards.Origin.of(origin.client, session or origin.session)


@router.get("/boards")
async def list_boards(
    collection: str | None = None, folder_path: str | None = None, include_archived: bool = False, p: Principal = Auth
) -> list[dict]:
    return await boards.list_boards(p, collection, folder_path, include_archived)


@router.post("/boards")
async def create_board(body: BoardIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.create_board(p, origin=origin, **body.model_dump())


@router.get("/boards/{board_id}")
async def get_board(board_id: str, include_done: bool = False, p: Principal = Auth) -> dict:
    return await boards.get_board(p, board_id, include_done=include_done)


@router.patch("/boards/{board_id}")
async def update_board(board_id: str, body: BoardPatch, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.update_board(p, board_id, origin=origin, **body.model_dump())


@router.get("/boards/{board_id}/log")
async def board_log(
    board_id: str,
    card_id: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = None,
    p: Principal = Auth,
) -> dict:
    return await boards.board_log(p, board_id, card_id, actor, kind, since, until, limit, before_id)


@router.post("/boards/{board_id}/columns")
async def create_column(board_id: str, body: ColumnIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.create_column(p, board_id, origin=origin, **body.model_dump())


@router.patch("/columns/{column_id}")
async def update_column(column_id: str, body: ColumnPatch, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.update_column(p, column_id, origin=origin, **body.model_dump())


@router.delete("/columns/{column_id}")
async def delete_column(
    column_id: str, move_cards_to: str | None = None, p: Principal = Auth, origin: boards.Origin = Origin
) -> dict:
    return await boards.delete_column(p, column_id, move_cards_to, origin=origin)


@router.post("/boards/{board_id}/cards")
async def create_card(board_id: str, body: CardIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.create_card(p, board_id, origin=origin, **body.model_dump())


@router.get("/my-work")
async def my_work(session: str | None = None, repo: str | None = None, p: Principal = Auth) -> dict:
    return await boards.my_work(p, session, repo)


@router.get("/cards/search")
async def search_cards(
    q: str,
    collection: str | None = None,
    include_done: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    p: Principal = Auth,
) -> list[dict]:
    return await boards.search_cards(p, q, collection, include_done, limit)


@router.get("/cards/{card_id}")
async def get_card(card_id: str, p: Principal = Auth) -> dict:
    return await boards.get_card(p, card_id)


@router.patch("/cards/{card_id}")
async def update_card(card_id: str, body: CardPatch, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    data = body.model_dump()
    return await boards.update_card(p, card_id, origin=_with_session(origin, data.pop("session")), **data)


@router.post("/cards/{card_id}/start")
async def start_card(card_id: str, body: StartIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.start_card(p, card_id, body.repo, body.force, origin=_with_session(origin, body.session))


@router.post("/cards/{card_id}/items")
async def add_items(card_id: str, body: ItemsIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.add_items(p, card_id, body.items, origin=_with_session(origin, body.session))


@router.post("/cards/{card_id}/check")
async def check_item(card_id: str, body: CheckIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.check_item(
        p, card_id, body.item_id, body.text, body.done, origin=_with_session(origin, body.session)
    )


@router.post("/cards/{card_id}/log")
async def log_card(card_id: str, body: CardLogIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.log_card(p, card_id, body.text, body.kind, origin=_with_session(origin, body.session))


@router.get("/cards/{card_id}/log")
async def card_log(card_id: str, limit: int = Query(default=100, ge=1, le=500), p: Principal = Auth) -> dict:
    return await boards.card_log(p, card_id, limit)


@router.post("/cards/{card_id}/block")
async def block_card(card_id: str, body: BlockIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.block_card(p, card_id, body.reason, origin=_with_session(origin, body.session))


@router.post("/cards/{card_id}/release")
async def release_card(card_id: str, body: ReleaseIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.release_card(p, card_id, body.next_step, body.force, origin=_with_session(origin, body.session))


@router.post("/cards/{card_id}/submit")
async def submit_card(card_id: str, body: SubmitIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.submit_card(p, card_id, body.summary, body.mr_url, origin=_with_session(origin, body.session))


@router.post("/cards/{card_id}/finish")
async def finish_card(card_id: str, body: FinishIn, p: Principal = Auth, origin: boards.Origin = Origin) -> dict:
    return await boards.finish_card(
        p, card_id, body.summary, body.append_to_document, origin=_with_session(origin, body.session)
    )


@router.post("/cards/{card_id}/documents")
async def link_card_document(
    card_id: str, body: CardDocumentIn, p: Principal = Auth, origin: boards.Origin = Origin
) -> dict:
    return await boards.link_card_document(p, card_id, body.document_id, body.unlink, origin=origin)


@router.get("/documents/{document_id}/cards")
async def document_cards(document_id: str, p: Principal = Auth) -> list[dict]:
    return await boards.cards_for_document(p, document_id)


# ---------------------------------------------------------------- gasto real das sessões (plan-web-38)


class SessionUsageIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=120)
    collection: str | None = None
    agent: str | None = None
    client: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=80)
    repo: str | None = Field(default=None, max_length=200)
    started_at: str | None = None
    ended_at: str | None = None
    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    concilium_calls: int = Field(default=0, ge=0)
    concilium_result_tokens: int = Field(default=0, ge=0)


@router.post("/usage/sessions")
async def report_session_usage(body: SessionUsageIn, p: Principal = Auth) -> dict:
    """Uso real de uma sessão do Claude Code (o plugin manda o acumulado; só contadores, sem conteúdo)."""
    return await usage.record_session(p, body.model_dump())
