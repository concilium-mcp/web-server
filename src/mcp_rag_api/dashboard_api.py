"""API da dashboard web (prefixo /dash/api) — consumida pela SPA em /dashboard.

Autenticação própria: sessão da dash via cookie HttpOnly (core/dash_auth.py),
separada do Bearer da API pública (api_keys continua valendo para agentes/integrações).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from .core import dash_auth, graph
from .core.dash_auth import DashUser
from .core.documents import parse_uuid
from .db import pool, records
from .security import NotFound

# ---------------------------------------------------------------- sessão


async def current_user(request: Request) -> DashUser:
    token = request.cookies.get(dash_auth.SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Sessão da dashboard ausente.")
    user = await dash_auth.resolve_session(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Sessão expirada ou revogada. Faça login de novo.")
    return user


async def require_admin(user: DashUser = Depends(current_user)) -> DashUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Ação restrita a administradores.")
    return user


class LoginIn(BaseModel):
    username: str
    password: str


auth_router = APIRouter(prefix="/dash/api/auth", tags=["dashboard-auth"])


@auth_router.post("/login")
async def login(body: LoginIn, response: Response) -> dict:
    user = await dash_auth.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
    token = await dash_auth.create_session(user.id)
    response.set_cookie(
        dash_auth.SESSION_COOKIE,
        token,
        max_age=int(dash_auth.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"id": user.id, "username": user.username, "role": user.role}


@auth_router.post("/logout")
async def logout(request: Request, response: Response, _user: DashUser = Depends(current_user)) -> dict:
    token = request.cookies.get(dash_auth.SESSION_COOKIE, "")
    await dash_auth.revoke_session(token)
    response.delete_cookie(dash_auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@auth_router.get("/me")
async def me(user: DashUser = Depends(current_user)) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role}


# ---------------------------------------------------------------- rotas protegidas


router = APIRouter(
    prefix="/dash/api",
    tags=["dashboard"],
    dependencies=[Depends(current_user)],
)


@router.get("/graph")
async def get_graph(
    collection: str | None = None,
    min_similarity: float = Query(default=0.7, ge=0.0, lt=1.0),
    k: int = Query(default=5, ge=1, le=20),
    level: Literal["documents", "chunks"] = "documents",
) -> dict:
    """Nós e arestas do grafo da base: vizinhança por cosseno (centroides ou chunks)."""
    return await graph.build_graph(level, collection, min_similarity, k)


@router.get("/documents/{document_id}")
async def get_document_detail(document_id: str) -> dict:
    """Painel lateral do grafo: documento com conteúdo, chunks e histórico de versões."""
    doc_uuid = parse_uuid(document_id, "document_id")
    async with pool().acquire() as conn:
        doc = await conn.fetchrow(
            """
            SELECT d.id, d.title, d.source, d.version, d.status, d.tags, d.metadata, d.content,
                   d.created_by, d.updated_by, d.created_at, d.updated_at, c.name AS collection
            FROM documents d
            JOIN collections c ON c.id = d.collection_id
            WHERE d.id = $1
            """,
            doc_uuid,
        )
        if doc is None:
            raise NotFound(f"Documento {document_id} não encontrado.")
        chunks = await conn.fetch(
            "SELECT chunk_index, word_count, content FROM chunks WHERE document_id = $1 ORDER BY chunk_index",
            doc_uuid,
        )
        versions = await conn.fetch(
            "SELECT version, changed_by, change_note, created_at FROM document_versions "
            "WHERE document_id = $1 ORDER BY version DESC",
            doc_uuid,
        )
    out = records([doc])[0]
    out["chunks"] = records(chunks)
    out["versions"] = records(versions)
    return out
