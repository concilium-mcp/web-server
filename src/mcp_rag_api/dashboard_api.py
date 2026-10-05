"""API da dashboard web (prefixo /dash/api) — consumida pela SPA em /dashboard.

Autenticação própria: sessão da dash via cookie HttpOnly (core/dash_auth.py),
separada do Bearer da API pública (api_keys continua valendo para agentes/integrações).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from .core import agents, dash_auth, documents, graph, links, search
from .core.dash_auth import DashUser
from .core.documents import parse_uuid
from .db import audit, pool, record, records
from .security import KBError, NotFound, Principal, create_api_key, validate_scopes

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


async def require_editor(user: DashUser = Depends(current_user)) -> DashUser:
    if not user.can_edit:
        raise HTTPException(status_code=403, detail="Seu papel é de leitura: peça a um admin o papel de editor.")
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
    min_similarity: float = Query(default=0.5, ge=0.0, lt=1.0),
    k: int = Query(default=5, ge=1, le=20),
    level: Literal["documents", "chunks"] = "documents",
) -> dict:
    """Nós e arestas do grafo da base: vizinhança por cosseno (centroides ou chunks)."""
    return await graph.build_graph(level, collection, min_similarity, k)


def _reader(user: DashUser) -> Principal:
    return Principal(actor=f"dash:{user.username}", scopes=frozenset({"read"}))


def _admin(user: DashUser) -> Principal:
    return Principal(actor=f"dash:{user.username}", scopes=frozenset({"admin"}))


def _writer(user: DashUser) -> Principal:
    return Principal(actor=f"dash:{user.username}", scopes=frozenset({"write"}))


# ---------------------------------------------------------------- notas (plan-web-02): documents pela dash

_DASH_CHANGE_NOTE = "edição pela dashboard"


def _who(actor: str | None) -> str:
    """Autor legível: "dash:maria" -> "maria", "key:kb_sk_x" -> "uma integração"."""
    if not actor:
        return "outra pessoa"
    if actor.startswith("dash:"):
        return actor.removeprefix("dash:")
    return "um agente/integração"


@router.get("/notes/tree")
async def notes_tree() -> dict:
    """Árvore leve da tela Notas: todas as coleções (inclusive vazias) e os documentos ativos, sem conteúdo."""
    collections = await pool().fetch(
        """
        SELECT c.name, c.description, count(d.id) FILTER (WHERE d.status = 'active') AS documents
        FROM collections c LEFT JOIN documents d ON d.collection_id = c.id
        GROUP BY c.id ORDER BY c.name
        """
    )
    notes = await pool().fetch(
        """
        SELECT d.id, d.title, c.name AS collection, d.tags, d.version, d.updated_at, d.updated_by
        FROM documents d JOIN collections c ON c.id = d.collection_id
        WHERE d.status = 'active'
        ORDER BY c.name, lower(d.title)
        """
    )
    return {"collections": records(collections), "notes": records(notes)}


@router.get("/notes/{document_id}")
async def get_note(document_id: str, user: DashUser = Depends(current_user)) -> dict:
    return await documents.get_document(_reader(user), document_id)


class NoteIn(BaseModel):
    collection: str
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = []
    force: bool = False


@router.post("/notes")
async def create_note(body: NoteIn, user: DashUser = Depends(require_editor)) -> dict:
    """Cria a nota. Se houver conteúdo muito parecido, devolve created=false + similar_document (a UI
    pergunta e repete com force=true)."""
    return await documents.add_document(
        _writer(user), body.collection, body.title, body.content, tags=body.tags, force=body.force
    )


class NotePatch(BaseModel):
    base_version: int  # versão que o editor abriu: trava otimista contra edição simultânea
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    change_note: str | None = None


@router.patch("/notes/{document_id}")
async def update_note(document_id: str, body: NotePatch, user: DashUser = Depends(require_editor)) -> dict:
    p = _writer(user)
    current = await documents.get_document(p, document_id)
    if current["version"] != body.base_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"A nota foi salva por {_who(current['updated_by'])} enquanto você editava.",
                "current_version": current["version"],
            },
        )
    return await documents.update_document(
        p,
        document_id,
        (body.change_note or "").strip() or _DASH_CHANGE_NOTE,
        content=body.content,
        title=body.title,
        tags=body.tags,
    )


class MoveIn(BaseModel):
    collection: str


@router.post("/notes/{document_id}/move")
async def move_note(document_id: str, body: MoveIn, user: DashUser = Depends(require_editor)) -> dict:
    return await documents.move_document(_writer(user), document_id, body.collection)


class ArchiveIn(BaseModel):
    reason: str = "arquivada pela dashboard"


@router.post("/notes/{document_id}/archive")
async def archive_note(document_id: str, body: ArchiveIn, user: DashUser = Depends(require_editor)) -> dict:
    return await documents.archive_document(_writer(user), document_id, body.reason)


@router.get("/notes/{document_id}/related")
async def note_related(document_id: str, user: DashUser = Depends(current_user)) -> dict:
    """Painel "Conexões" da nota: links, backlinks e vizinhos semânticos."""
    return await links.get_related(_reader(user), document_id, 6)


@router.get("/notes/{document_id}/versions")
async def note_versions(document_id: str, user: DashUser = Depends(current_user)) -> list[dict]:
    return await documents.document_history(_reader(user), document_id)


@router.get("/notes/{document_id}/versions/{version}")
async def note_version(document_id: str, version: int, user: DashUser = Depends(current_user)) -> dict:
    return await documents.get_document_version(_reader(user), document_id, version)


@router.post("/notes/{document_id}/restore/{version}")
async def restore_note(document_id: str, version: int, user: DashUser = Depends(require_editor)) -> dict:
    return await documents.restore_document_version(_writer(user), document_id, version)


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None


@router.post("/collections")
async def create_collection(body: CollectionIn, user: DashUser = Depends(require_editor)) -> dict:
    return await documents.create_collection(_writer(user), body.name, body.description)


@router.get("/documents/titles")
async def document_titles(q: str = "", limit: int = Query(default=10, ge=1, le=50)) -> list[dict]:
    """Autocomplete leve do "Conectar a…": título/coleção dos documentos ativos (sem conteúdo)."""
    rows = await pool().fetch(
        """
        SELECT d.id AS document_id, d.title, c.name AS collection
        FROM documents d JOIN collections c ON c.id = d.collection_id
        WHERE d.status = 'active' AND ($1 = '' OR kb_title_key(d.title) LIKE '%' || kb_title_key($1) || '%')
        ORDER BY d.updated_at DESC LIMIT $2
        """,
        q.strip(),
        limit,
    )
    return records(rows)


@router.get("/documents/{document_id}/links")
async def document_links(document_id: str, user: DashUser = Depends(current_user)) -> dict:
    """Links que saem do documento (inclusive [[pendentes]]) e backlinks."""
    return await links.get_links(_reader(user), document_id)


class LinkIn(BaseModel):
    target_id: str
    note: str | None = None


@router.post("/documents/{document_id}/links")
async def create_link(document_id: str, body: LinkIn, user: DashUser = Depends(require_admin)) -> dict:
    return await links.link_documents(_admin(user), document_id, body.target_id, body.note)


@router.delete("/links/{link_id}")
async def delete_link(link_id: int, user: DashUser = Depends(require_admin)) -> dict:
    return await links.unlink_documents(_admin(user), link_id)


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


# ---------------------------------------------------------------- agentes


@router.get("/agents")
async def list_agents(_user: DashUser = Depends(current_user)) -> list[dict]:
    """Agents cadastrados, com contagem de propostas pendentes e chaves ativas."""
    rows = await pool().fetch(
        """
        SELECT a.slug, a.name, a.description, a.status, a.scopes, a.allowed_collections,
               a.version, a.updated_at, a.config,
               (SELECT count(*) FROM agent_versions v WHERE v.agent_id = a.id AND v.status = 'proposed') AS proposals,
               (SELECT count(*) FROM api_keys k WHERE k.agent_id = a.id AND k.revoked_at IS NULL) AS active_keys
        FROM agents a
        ORDER BY a.slug
        """
    )
    items = records(rows)
    for item in items:
        item["auto_apply_updates"] = bool((item.pop("config") or {}).get("auto_apply_updates"))
    return items


class AgentCreateIn(BaseModel):
    slug: str
    name: str
    system_prompt: str
    description: str | None = None
    allowed_collections: list[str] = []
    scopes: list[str] = ["read", "write"]
    auto_apply_updates: bool = False


@router.post("/agents")
async def create_agent(body: AgentCreateIn, user: DashUser = Depends(require_admin)) -> dict:
    """Cadastra um agente pela dash — mesma regra do create_agent do MCP/REST (versão 1 + chave do agente).

    Devolve a API key do agente (exibida uma única vez) e o connect_command."""
    admin = Principal(actor=f"dash:{user.username}", scopes=frozenset({"admin"}))
    return await agents.create_agent(
        admin,
        slug=body.slug,
        name=body.name,
        system_prompt=body.system_prompt,
        description=body.description,
        config={"auto_apply_updates": body.auto_apply_updates},
        allowed_collections=body.allowed_collections,
        scopes=body.scopes,
    )


@router.get("/collections")
async def list_collections(user: DashUser = Depends(current_user)) -> list[dict]:
    """Coleções da base (nome, descrição, nº de documentos) — usadas no formulário de agente."""
    return await documents.list_collections(Principal(actor=f"dash:{user.username}", scopes=frozenset({"read"})))


class AutonomyIn(BaseModel):
    auto_apply_updates: bool


@router.post("/agents/{slug}/autonomy")
async def set_autonomy(
    slug: str, body: AutonomyIn, user: DashUser = Depends(require_admin)
) -> dict:
    """Liga/desliga a autonomia (auto_apply_updates) de um agente — ação de admin."""
    admin = Principal(actor=f"dash:{user.username}", scopes=frozenset({"admin"}))
    return await agents.set_agent_autonomy(admin, slug, body.auto_apply_updates)


# ---------------------------------------------------------------- chaves Bearer


@router.get("/keys")
async def list_keys(_user: DashUser = Depends(current_user)) -> list[dict]:
    """Todas as chaves da API (prefixo, label, escopos, agente, último uso, status)."""
    rows = await pool().fetch(
        """
        SELECT k.id, k.prefix, k.label, k.scopes, k.revoked_at, k.last_used_at, k.created_at,
               a.slug AS agent
        FROM api_keys k
        LEFT JOIN agents a ON a.id = k.agent_id
        ORDER BY k.created_at DESC
        """
    )
    return records(rows)


class KeyIn(BaseModel):
    label: str
    scopes: list[str] = ["read"]
    agent_slug: str | None = None


@router.post("/keys")
async def create_key(body: KeyIn, user: DashUser = Depends(require_admin)) -> dict:
    """Cria uma chave humana ou de agente. O material da chave só aparece desta vez."""
    scopes = validate_scopes(body.scopes)
    async with pool().acquire() as conn, conn.transaction():
        agent_id = None
        if body.agent_slug:
            agent_id = await conn.fetchval("SELECT id FROM agents WHERE slug = $1", body.agent_slug)
            if agent_id is None:
                raise NotFound(f"Agente '{body.agent_slug}' não encontrado.")
        key = await create_api_key(conn, label=body.label, scopes=scopes, agent_id=agent_id)
        await audit(conn, f"dash:{user.username}", "dash.key.create", key["prefix"], agent=body.agent_slug)
    return key


@router.post("/keys/{key_id}/revoke")
async def revoke_key(key_id: str, user: DashUser = Depends(require_admin)) -> dict:
    key_uuid = parse_uuid(key_id, "key_id")
    async with pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL "
            "RETURNING prefix, agent_id",
            key_uuid,
        )
        if row is None:
            raise NotFound("Chave não encontrada ou já revogada.")
        await audit(conn, f"dash:{user.username}", "dash.key.revoke", row["prefix"])
    return {"revoked": True, "prefix": row["prefix"]}


@router.post("/keys/{key_id}/renew")
async def renew_key(key_id: str, user: DashUser = Depends(require_admin)) -> dict:
    """Revoga a chave e emite uma nova com o mesmo label/escopos/agente."""
    key_uuid = parse_uuid(key_id, "key_id")
    async with pool().acquire() as conn, conn.transaction():
        old = await conn.fetchrow("SELECT * FROM api_keys WHERE id = $1", key_uuid)
        if old is None:
            raise NotFound("Chave não encontrada.")
        if old["revoked_at"] is not None:
            raise KBError("A chave já está revogada; crie uma nova em vez de renovar.")
        await conn.execute("UPDATE api_keys SET revoked_at = now() WHERE id = $1", key_uuid)
        key = await create_api_key(
            conn, label=old["label"], scopes=list(old["scopes"]), agent_id=old["agent_id"]
        )
        await audit(conn, f"dash:{user.username}", "dash.key.renew", old["prefix"], new_prefix=key["prefix"])
    return {"revoked_key_id": str(key_uuid), **key}


# ---------------------------------------------------------------- usuários da dash


@router.get("/users")
async def list_users(_admin: DashUser = Depends(require_admin)) -> list[dict]:
    rows = await pool().fetch(
        "SELECT id, username, role, disabled_at, created_at FROM dash_users ORDER BY username"
    )
    return records(rows)


class DashUserIn(BaseModel):
    username: str
    password: str
    role: Literal["admin", "editor", "viewer"] = "viewer"


@router.post("/users")
async def create_dash_user(body: DashUserIn, admin: DashUser = Depends(require_admin)) -> dict:
    created = await dash_auth.create_user(body.username, body.password, body.role)
    async with pool().acquire() as conn:
        await audit(conn, f"dash:{admin.username}", "dash.user.create", created["username"], role=body.role)
    return created


class DashUserPatch(BaseModel):
    role: Literal["admin", "editor", "viewer"] | None = None
    disabled: bool | None = None
    password: str | None = None


@router.patch("/users/{user_id}")
async def update_dash_user(
    user_id: str, body: DashUserPatch, admin: DashUser = Depends(require_admin)
) -> dict:
    target = parse_uuid(user_id, "user_id")
    if not (body.role is not None or body.disabled is not None or body.password is not None):
        raise KBError("Nada para alterar: informe role, disabled ou password.")
    row = await pool().fetchrow("SELECT id, username FROM dash_users WHERE id = $1", target)
    if row is None:
        raise NotFound(f"Usuário {user_id} não encontrado.")
    if str(row["id"]) == admin.id and (
        (body.role is not None and body.role != "admin") or body.disabled
    ):
        raise KBError("Use outra conta admin para rebaixar ou desativar a si mesmo.")

    sets, args = [], []

    def add(fragment: str, value=None) -> None:
        if "${n}" in fragment:
            args.append(value)
            fragment = fragment.format(n=len(args))
        sets.append(fragment)

    if body.role is not None:
        add("role = ${n}", body.role)
    if body.disabled is not None:
        add("disabled_at = CASE WHEN ${n} THEN now() ELSE NULL END", body.disabled)
    if body.password is not None:
        if len(body.password) < 8:
            raise KBError("A senha deve ter ao menos 8 caracteres.")
        add("password_hash = ${n}", dash_auth.hash_password(body.password))
    add("updated_at = now()")
    args.append(target)
    await pool().execute(
        f"UPDATE dash_users SET {', '.join(sets)} WHERE id = ${len(args)}", *args
    )
    if body.disabled or body.password is not None:
        await dash_auth.revoke_user_sessions(str(row["id"]))
    async with pool().acquire() as conn:
        await audit(
            conn,
            f"dash:{admin.username}",
            "dash.user.update",
            row["username"],
            role=body.role,
            disabled=body.disabled,
            password_reset=body.password is not None,
        )
    updated = await pool().fetchrow(
        "SELECT id, username, role, disabled_at, created_at FROM dash_users WHERE id = $1", target
    )
    return record(updated)


# ---------------------------------------------------------------- extras: stats e testador de busca


@router.get("/stats")
async def get_stats(_user: DashUser = Depends(current_user)) -> dict:
    """Contagens da base para os cards da tela inicial da dash."""
    row = await pool().fetchrow(
        """
        SELECT (SELECT count(*) FROM collections) AS collections,
               (SELECT count(*) FROM documents WHERE status = 'active') AS documents,
               (SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id
                 WHERE d.status = 'active') AS chunks,
               (SELECT count(*) FROM agents WHERE status = 'active') AS agents,
               (SELECT count(*) FROM agent_memories) AS memories,
               (SELECT count(*) FROM agent_versions WHERE status = 'proposed') AS proposals,
               (SELECT count(*) FROM api_keys WHERE revoked_at IS NULL) AS active_keys
        """
    )
    return records([row])[0]


class SearchTestIn(BaseModel):
    query: str
    collections: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/search-test")
async def search_test(body: SearchTestIn, user: DashUser = Depends(current_user)) -> dict:
    """Roda a busca híbrida real da base e devolve os chunks, para destacar no grafo."""
    principal = Principal(actor=f"dash:{user.username}", scopes=frozenset({"read"}))
    results = await search.search_knowledge(principal, body.query, body.collections, None, None, body.top_k)
    return {"results": results}
