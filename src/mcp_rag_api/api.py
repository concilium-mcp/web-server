"""API REST: espelho das tools MCP para integrações que não falam MCP."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from . import db
from .config import get_settings
from .core import agents, documents, memory, search
from .security import DEV_PRINCIPAL, PermissionDenied, Principal, bearer_token, resolve_key

router = APIRouter()


async def principal(authorization: str | None = Header(default=None)) -> Principal:
    token = bearer_token(authorization)
    if token:
        async with db.pool().acquire() as conn:
            return await resolve_key(conn, token)
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


class DocumentPatch(BaseModel):
    change_note: str
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: str | None = None


class DocumentUpsert(BaseModel):
    collection: str
    external_id: str
    title: str
    content: str
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: str | None = None
    change_note: str = "sincronização (upsert)"


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
    return await documents.add_document(p, **body.model_dump())


@router.put("/documents/upsert")
async def upsert_document(body: DocumentUpsert, p: Principal = Auth) -> dict:
    return await documents.upsert_document(p, **body.model_dump())


@router.get("/documents/{document_id}")
async def get_document(document_id: str, p: Principal = Auth) -> dict:
    return await documents.get_document(p, document_id)


@router.patch("/documents/{document_id}")
async def update_document(document_id: str, body: DocumentPatch, p: Principal = Auth) -> dict:
    return await documents.update_document(p, document_id, **body.model_dump())


@router.delete("/documents/{document_id}")
async def archive_document(document_id: str, reason: str = "arquivado via API", p: Principal = Auth) -> dict:
    return await documents.archive_document(p, document_id, reason)


@router.get("/documents/{document_id}/versions")
async def document_history(document_id: str, p: Principal = Auth) -> list[dict]:
    return await documents.document_history(p, document_id)


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


@router.get("/agents/{slug}/context")
async def agent_context(slug: str, p: Principal = Auth) -> dict:
    return await memory.load_agent(p, slug)


@router.get("/agents/{slug}/memories")
async def recall(slug: str, query: str, limit: int = 8, p: Principal = Auth) -> list[dict]:
    return await memory.recall(p, query, slug, limit=limit)


@router.post("/agents/{slug}/memories")
async def remember(slug: str, body: MemoryIn, p: Principal = Auth) -> dict:
    return await memory.remember(p, agent_slug=slug, **body.model_dump())


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
