"""API REST: espelho das tools MCP para integrações que não falam MCP."""

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from . import db
from .config import get_settings
from .core import agents, documents, file_import, folders, links, memory, search
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
async def search_knowledge(body: SearchIn, p: Principal = Auth) -> list[dict]:
    return await search.search_knowledge(p, body.query, body.collections, body.tags, body.metadata, body.top_k)


@router.get("/documents")
async def list_documents(
    collection: str | None = None,
    tags: list[str] | None = Query(default=None),
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
    p: Principal = Auth,
) -> list[dict]:
    return await documents.list_documents(p, collection, tags, status, limit, offset)


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
    converted = await asyncio.to_thread(file_import.convert_file, name, data)
    return await file_import.import_file(p, collection, folder_path or None, converted)


@router.get("/documents/{document_id}")
async def get_document(document_id: str, p: Principal = Auth) -> dict:
    return await folders.get_document_with_folder(p, document_id)


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
