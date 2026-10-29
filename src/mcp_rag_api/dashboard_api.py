"""API da dashboard web (prefixo /dash/api) — consumida pela SPA em /dashboard.

Autenticação própria: sessão da dash via cookie HttpOnly (core/dash_auth.py),
separada do Bearer da API pública (api_keys continua valendo para agentes/integrações).
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .config import get_settings
from .core import agents, dash_auth, documents, file_import, folders, graph, links, mfa, oauth, obsidian_import, search
from .core.dash_auth import DashUser
from .core.documents import parse_uuid
from .db import audit, pool, record, records
from .logging_config import get_logger
from .security import KBError, NotFound, Principal, VersionConflict, create_api_key, validate_scopes

logger = get_logger()

# ---------------------------------------------------------------- sessão

# Defesa CSRF em profundidade: além do cookie SameSite=Lax, toda mutação da dash precisa
# deste header customizado (o dashboard.js o envia em todos os fetch). Form/simple request
# cross-site não consegue setar header, então o POST forjado morre aqui com 403.
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "fetch"


async def require_csrf_header(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Requisição sem o cabeçalho X-Requested-With.")


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
    # rota anônima: sem teto, o corpo vira memória presa no rate limit e linha de log
    username: str = Field(max_length=254)
    password: str = Field(max_length=1024)


auth_router = APIRouter(
    prefix="/dash/api/auth",
    tags=["dashboard-auth"],
    dependencies=[Depends(require_csrf_header)],
)


def client_ip(request: Request) -> str:
    """IP do cliente para o rate limit. Atrás do Cloudflare vale o CF-Connecting-IP (o Cloudflare
    sobrescreve o que vier do cliente); o request.client.host sai do X-Forwarded-For mais à
    esquerda (--forwarded-allow-ips='*'), que o próprio cliente consegue forjar. Sem Cloudflare na
    frente, TRUST_CF_CONNECTING_IP=false faz o cabeçalho ser ignorado."""
    cf_ip = request.headers.get("cf-connecting-ip", "").strip() if get_settings().trust_cf_connecting_ip else ""
    return cf_ip or (request.client.host if request.client else "?")


def _login_buckets(username: str, request: Request) -> list[tuple[dash_auth.LoginRateLimiter, tuple[str, str]]]:
    return [
        (dash_auth.login_rate_limiter(), (username, client_ip(request))),
        (dash_auth.login_user_rate_limiter(), (username, "*")),
    ]


def _check_login_blocked(buckets: list[tuple[dash_auth.LoginRateLimiter, tuple[str, str]]]) -> None:
    blocked = max(limiter.blocked_for(key) for limiter, key in buckets)
    if blocked:
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas de login. Tente novamente em {int(blocked) + 1}s.",
        )


@auth_router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    username = body.username.strip().lower()
    buckets = _login_buckets(username, request)
    _check_login_blocked(buckets)
    user = await dash_auth.authenticate(body.username, body.password)
    if user is None:
        for limiter, key in buckets:
            limiter.register_failure(key)
        logger.warning("dash auth: login falhou (username=%s ip=%s)", username, client_ip(request))
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
    if await mfa.is_enabled(user.id):
        # senha certa não zera o rate limit: quem tem só a senha não ganha tentativas de código à vontade
        logger.info("dash auth: senha ok, aguardando 2FA (username=%s)", user.username)
        return {"mfa_required": True, "mfa_token": await mfa.create_challenge(user.id)}
    for limiter, key in buckets:
        limiter.reset(key)
    return await _start_session(user, request, response)


class MfaLoginIn(BaseModel):
    mfa_token: str
    code: str


@auth_router.post("/login/mfa")
async def login_mfa(body: MfaLoginIn, request: Request, response: Response) -> dict:
    """2ª etapa do login: código do app autenticador ou um recovery code."""
    who = await mfa.claim_challenge_attempt(body.mfa_token)  # já conta a tentativa
    if who is None:
        raise HTTPException(status_code=401, detail="Verificação expirada. Entre com a senha de novo.")
    buckets = _login_buckets(who["username"], request)
    _check_login_blocked(buckets)
    method = await mfa.verify(who["id"], body.code)
    if method is None:
        for limiter, key in buckets:
            limiter.register_failure(key)
        logger.warning("dash auth: código 2FA inválido (username=%s ip=%s)", who["username"], client_ip(request))
        raise HTTPException(status_code=400, detail="Código inválido.")
    await mfa.consume_challenge(body.mfa_token)
    for limiter, key in buckets:
        limiter.reset(key)
    if method == "recovery":
        async with pool().acquire() as conn:
            await audit(conn, f"dash:{who['username']}", "dash.2fa.recovery_used", who["username"])
    return await _start_session(DashUser(**who), request, response)


async def _start_session(user: DashUser, request: Request, response: Response) -> dict:
    token = await dash_auth.create_session(user.id)
    # cookie Secure só em HTTPS: em produção o uvicorn roda atrás de proxy com
    # --proxy-headers (X-Forwarded-Proto chega no scheme da request); em dev local (HTTP)
    # o cookie continua funcionando sem Secure.
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    secure = request.url.scheme == "https" or forwarded_proto == "https"
    logger.info("dash auth: login ok (username=%s role=%s)", user.username, user.role)
    response.set_cookie(
        dash_auth.SESSION_COOKIE,
        token,
        max_age=int(dash_auth.session_ttl().total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return {"id": user.id, "username": user.username, "role": user.role}


@auth_router.post("/logout")
async def logout(request: Request, response: Response, _user: DashUser = Depends(current_user)) -> dict:
    token = request.cookies.get(dash_auth.SESSION_COOKIE, "")
    await dash_auth.revoke_session(token)
    logger.info("dash auth: logout (username=%s)", _user.username)
    response.delete_cookie(dash_auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@auth_router.get("/me")
async def me(user: DashUser = Depends(current_user)) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role}


# ---------------------------------------------------------------- rotas protegidas


router = APIRouter(
    prefix="/dash/api",
    tags=["dashboard"],
    dependencies=[Depends(require_csrf_header), Depends(current_user)],
)


@router.get("/graph")
async def get_graph(
    collection: str | None = None,
    min_similarity: float = Query(default=0.5, ge=0.0, lt=1.0),
    k: int = Query(default=5, ge=1, le=20),
    level: Literal["documents", "chunks"] = "documents",
    folder_id: str | None = None,
    view: Literal["auto", "all", "groups"] = "auto",
    group: int | None = Query(default=None, ge=0),
) -> dict:
    """Nós e arestas do grafo da base: vizinhança por cosseno (centroides ou chunks). folder_id = só a
    pasta e as subpastas. No nível documentos os nós vêm com x,y do layout do servidor; view="groups"
    (padrão acima de ~1.500 notas em "auto") devolve um nó por grupo de assunto e group=N, as notas dele."""
    return await graph.build_graph(level, collection, min_similarity, k, folder_id or None, view=view, group=group)


@router.get("/folders")
async def location_tree(user: DashUser = Depends(current_user)) -> dict:
    """Coleções e pastas (caminho, nº de notas diretas e com subpastas) para o seletor de local."""
    return await folders.location_tree(_reader(user))


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
async def notes_tree(user: DashUser = Depends(current_user)) -> dict:
    """Árvore leve da tela Notas: todas as coleções (inclusive vazias), as pastas aninhadas dentro delas
    (plan-web-14) e os documentos ativos com a pasta onde estão (folder_id NULL = raiz), sem conteúdo."""
    collections = await pool().fetch(
        """
        SELECT c.name, c.description, count(d.id) FILTER (WHERE d.status = 'active') AS documents
        FROM collections c LEFT JOIN documents d ON d.collection_id = c.id
        GROUP BY c.id ORDER BY c.name
        """
    )
    notes = await pool().fetch(
        """
        SELECT d.id, d.title, c.name AS collection, d.folder_id, d.tags, d.version, d.updated_at, d.updated_by
        FROM documents d JOIN collections c ON c.id = d.collection_id
        WHERE d.status = 'active'
        ORDER BY c.name, lower(d.title)
        """
    )
    return {
        "collections": records(collections),
        "folders": await folders.list_folders(_reader(user)),
        "notes": records(notes),
    }


@router.get("/notes/{document_id}")
async def get_note(document_id: str, user: DashUser = Depends(current_user)) -> dict:
    return await documents.get_document(_reader(user), document_id)


class NoteIn(BaseModel):
    collection: str
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = []
    force: bool = False
    folder_id: str | None = None  # pasta dentro da coleção (None = raiz)


@router.post("/notes")
async def create_note(body: NoteIn, user: DashUser = Depends(require_editor)) -> dict:
    """Cria a nota. Se houver conteúdo muito parecido, devolve created=false + similar_document (a UI
    pergunta e repete com force=true)."""
    writer = _writer(user)
    if body.folder_id:
        await folders.ensure_folder_in(writer, body.folder_id, body.collection)  # valida antes de criar
    result = await documents.add_document(
        writer, body.collection, body.title, body.content, tags=body.tags, force=body.force
    )
    if body.folder_id and result.get("created"):
        await folders.place_document(writer, result["document_id"], body.folder_id)
        result["folder_id"] = body.folder_id
    return result


class NotePatch(BaseModel):
    base_version: int  # versão que o editor abriu: trava otimista contra edição simultânea
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    change_note: str | None = None


@router.patch("/notes/{document_id}")
async def update_note(document_id: str, body: NotePatch, user: DashUser = Depends(require_editor)) -> dict:
    # Sem leitura prévia: a trava otimista é a própria query de UPDATE (base_version), e o
    # conflito — inclusive em race — chega aqui como VersionConflict com a versão atual.
    try:
        return await documents.update_document(
            _writer(user),
            document_id,
            (body.change_note or "").strip() or _DASH_CHANGE_NOTE,
            content=body.content,
            title=body.title,
            tags=body.tags,
            base_version=body.base_version,
        )
    except VersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"A nota foi salva por {_who(exc.actor)} enquanto você editava.",
                "current_version": exc.current_version,
            },
        ) from exc


class MoveIn(BaseModel):
    collection: str
    folder_id: str | None = None  # pasta de destino dentro da coleção (None = raiz)


@router.post("/notes/{document_id}/move")
async def move_note(document_id: str, body: MoveIn, user: DashUser = Depends(require_editor)) -> dict:
    writer = _writer(user)
    if body.folder_id:
        await folders.ensure_folder_in(writer, body.folder_id, body.collection)
    result = await documents.move_document(writer, document_id, body.collection)
    placed = await folders.place_document(writer, document_id, body.folder_id)
    return {**result, "moved": True, "folder_id": placed["folder_id"]}


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


class CollectionRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.patch("/collections/{name}")
async def rename_collection(name: str, body: CollectionRenameIn, user: DashUser = Depends(require_admin)) -> dict:
    """Renomeia a pasta principal (plan-web-18) — ação de admin."""
    admin = Principal(actor=f"dash:{user.username}", scopes=frozenset({"admin"}))
    return await folders.rename_collection(admin, name, body.name)


@router.delete("/collections/{name}")
async def delete_collection(name: str, user: DashUser = Depends(require_admin)) -> dict:
    """Exclui a pasta principal sem notas ativas (plan-web-18) — ação de admin, irreversível."""
    admin = Principal(actor=f"dash:{user.username}", scopes=frozenset({"admin"}))
    return await folders.delete_collection(admin, name)


# ---------------------------------------------------------------- pastas aninhadas (plan-web-14)


class FolderIn(BaseModel):
    collection: str
    name: str = Field(min_length=1, max_length=folders.FOLDER_NAME_MAX)
    parent_id: str | None = None  # None = direto na coleção (pasta principal)


@router.post("/folders")
async def create_folder(body: FolderIn, user: DashUser = Depends(require_editor)) -> dict:
    return await folders.create_folder(_writer(user), body.collection, body.name, body.parent_id)


class FolderPatch(BaseModel):
    name: str = Field(min_length=1, max_length=folders.FOLDER_NAME_MAX)


@router.patch("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: FolderPatch, user: DashUser = Depends(require_editor)) -> dict:
    return await folders.rename_folder(_writer(user), folder_id, body.name)


class FolderMoveIn(BaseModel):
    collection: str
    parent_id: str | None = None


@router.post("/folders/{folder_id}/move")
async def move_folder(folder_id: str, body: FolderMoveIn, user: DashUser = Depends(require_editor)) -> dict:
    return await folders.move_folder(_writer(user), folder_id, body.collection, body.parent_id)


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, user: DashUser = Depends(require_editor)) -> dict:
    """Remove a pasta; o conteúdo sobe um nível (nada é apagado)."""
    return await folders.delete_folder(_writer(user), folder_id)


# ---------------------------------------------------------------- importação: vault (plan-web-13) e arquivos (web-28)

# Job assíncrono em memória (deploy é single-process; mesmo precedente do LoginRateLimiter).
# Restart perde o job — aceitável porque o re-import é idempotente (external_id = path no vault).
JOB_RETENTION = timedelta(hours=1)
_IMPORT_CHUNK_SIZE = 1024 * 1024


class ImportJob:
    """Estado de um import: atualizado pela task em background, lido pelo poll da dash."""

    def __init__(self, job_id: str, username: str, total: int) -> None:
        self.id = job_id
        self.username = username
        self.status = "running"  # running | done | cancelled | error
        self.total = total
        self.done = 0
        self.current_file: str | None = None
        self.collections: set[str] = set()
        self.notes = 0
        self.errors: list[dict] = []
        self.cancel_requested = False
        self.finished_at: datetime | None = None

    def snapshot(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "current_file": self.current_file,
            "collections": len(self.collections),
            "notes": self.notes,
            "errors": self.errors,
        }


_import_jobs: dict[str, ImportJob] = {}
_import_tasks: set[asyncio.Task[None]] = set()  # refs fortes: task pendente pode ser coletada pelo GC


def _cleanup_finished_jobs() -> None:
    """Jobs finalizados ficam ~1h em memória (o poll final lê o resumo) e depois evaporam."""
    now = datetime.now(UTC)
    for job_id, job in list(_import_jobs.items()):
        if job.finished_at is not None and now - job.finished_at > JOB_RETENTION:
            del _import_jobs[job_id]


def _job_for(user: DashUser, job_id: str) -> ImportJob:
    job = _import_jobs.get(job_id)
    # 404 também para job de outra pessoa: não expõe a existência do job (id opaco, dono ou admin).
    if job is None or (job.username != user.username and not user.is_admin):
        raise NotFound("Importação não encontrada (os jobs ficam ~1h em memória).")
    return job


async def _read_upload_limited(file: UploadFile, limit: int, too_big: str) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_IMPORT_CHUNK_SIZE):
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=too_big)
        chunks.append(chunk)
    return b"".join(chunks)


async def _run_import[T](
    job: ImportJob,
    items: list[T],
    name_of: Callable[[T], str],
    handle: Callable[[T], Awaitable[str]],
) -> None:
    """Vetorização sequencial (providers de embeddings dão 429 com concorrência).

    `handle` grava um item (chunk + embed + dedupe + sync de wikilinks pelo core) e devolve a
    coleção de destino; o cancelamento é cooperativo, observado entre um item e outro.
    """
    try:
        for item in items:
            if job.cancel_requested:
                job.status = "cancelled"
                break
            job.current_file = name_of(item)
            try:
                collection = await handle(item)
            except Exception as exc:  # um arquivo ruim não derruba o import inteiro
                job.errors.append({"file": job.current_file, "error": str(exc)})
            else:
                job.collections.add(collection)
                job.notes += 1
            job.done += 1
        else:
            job.status = "done"
    except asyncio.CancelledError:
        # task derrubada (shutdown do processo/loop): não fica "running" para sempre
        job.status = "cancelled"
        raise
    except Exception as exc:
        job.status = "error"
        job.errors.append({"file": None, "error": f"Falha inesperada do import: {exc}"})
    finally:
        job.current_file = None
        job.finished_at = datetime.now(UTC)
        logger.info(
            "dash import: %s (username=%s, notes=%d/%d, collections=%d, errors=%d)",
            job.status,
            job.username,
            job.notes,
            job.total,
            len(job.collections),
            len(job.errors),
        )


def _ensure_no_active_import(user: DashUser) -> None:
    """Um import ativo por usuário."""
    _cleanup_finished_jobs()
    active = next(
        (j for j in _import_jobs.values() if j.username == user.username and j.status == "running"),
        None,
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe uma importação em andamento ({active.done}/{active.total}). "
            "Acompanhe-a ou cancele antes de iniciar outra.",
        )


def _start_import[T](
    user: DashUser, items: list[T], name_of: Callable[[T], str], handle: Callable[[T], Awaitable[str]]
) -> dict:
    job = ImportJob(secrets.token_urlsafe(12), user.username, len(items))
    _import_jobs[job.id] = job
    task = asyncio.create_task(_run_import(job, items, name_of, handle))
    _import_tasks.add(task)
    task.add_done_callback(_import_tasks.discard)
    logger.info("dash import: iniciado (username=%s, total=%d)", user.username, job.total)
    return {"job_id": job.id, "total": job.total}


@router.post("/import/obsidian")
async def import_obsidian(file: UploadFile = File(...), user: DashUser = Depends(require_editor)) -> dict:
    """Recebe o .zip do vault, valida e dispara o import em background (um ativo por usuário)."""
    _ensure_no_active_import(user)
    data = await _read_upload_limited(
        file,
        obsidian_import.MAX_ZIP_BYTES,
        f"O zip passa de {obsidian_import.MAX_ZIP_BYTES // (1024 * 1024)} MB. Divida o vault e importe em partes.",
    )
    notes = obsidian_import.parse_vault(data)  # KBError (zip inválido/sem notas/limites) vira 400
    writer = _writer(user)

    async def handle(note: obsidian_import.ParsedNote) -> str:
        await documents.create_collection(writer, note.collection)
        await documents.upsert_document(
            writer,
            note.collection,
            note.path,
            note.title,
            note.content,
            tags=note.tags or None,
            source="obsidian-import",
        )
        return note.collection

    return _start_import(user, notes, lambda n: n.path, handle)


@router.post("/import/files")
async def import_files(
    files: list[UploadFile] = File(...),
    collection: str = Form(...),
    folder_id: str | None = Form(None),
    user: DashUser = Depends(require_editor),
) -> dict:
    """PDF, Word (.docx), Markdown e texto soltos → notas numa coleção/pasta (plan-web-28).

    Formato e tamanho são validados antes de começar; a conversão roda dentro do job (em
    thread, é CPU) para que um arquivo ilegível vire erro daquele arquivo, não do import todo.
    """
    _ensure_no_active_import(user)
    writer = _writer(user)
    writer.require_collection(collection)
    if not files:
        raise KBError("Nenhum arquivo enviado.")
    if len(files) > file_import.MAX_FILES:
        raise KBError(f"Envie no máximo {file_import.MAX_FILES} arquivos por vez.")
    for upload in files:
        file_import.file_type_for(file_import.safe_filename(upload.filename or ""))
    folder_path: str | None = None
    if folder_id:
        await folders.ensure_folder_in(writer, folder_id, collection)
        in_collection = await folders.list_folders(writer, collection)
        folder_path = next(f["path"] for f in in_collection if str(f["id"]) == folder_id)
    pending: list[tuple[str, bytes]] = []
    total = 0
    for upload in files:
        name = file_import.safe_filename(upload.filename or "")
        mb = file_import.MAX_FILE_BYTES // (1024 * 1024)
        data = await _read_upload_limited(upload, file_import.MAX_FILE_BYTES, f"'{name}' passa de {mb} MB.")
        total += len(data)
        if total > file_import.MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"O envio passa de {file_import.MAX_TOTAL_BYTES // (1024 * 1024)} MB. Importe em partes.",
            )
        pending.append((name, data))

    engine, max_ocr_pages = file_import.ocr_options()

    async def handle(item: tuple[str, bytes]) -> str:
        converted = await asyncio.to_thread(file_import.convert_file, *item, engine, max_ocr_pages)
        await file_import.import_file(writer, collection, folder_path, converted)
        return collection

    return _start_import(user, pending, lambda item: item[0], handle)


@router.get("/import/{job_id}")
async def import_status(job_id: str, user: DashUser = Depends(current_user)) -> dict:
    return _job_for(user, job_id).snapshot()


@router.post("/import/{job_id}/cancel")
async def import_cancel(job_id: str, user: DashUser = Depends(require_editor)) -> dict:
    job = _job_for(user, job_id)
    if job.status == "running":
        job.cancel_requested = True
    return {"status": job.status}


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
async def set_autonomy(slug: str, body: AutonomyIn, user: DashUser = Depends(require_admin)) -> dict:
    """Liga/desliga a autonomia (auto_apply_updates) de um agente — ação de admin."""
    admin = Principal(actor=f"dash:{user.username}", scopes=frozenset({"admin"}))
    return await agents.set_agent_autonomy(admin, slug, body.auto_apply_updates)


# ---------------------------------------------------------------- gerenciar agente (plan-web-16)
# Editar, chaves e arquivar — mesmas regras do MCP/REST (core/agents.py): cada edição gera uma versão
# nova do perfil, com histórico.

_DASH_AGENT_NOTE = "edição pela dashboard"


def _agent_admin(user: DashUser) -> Principal:
    return Principal(actor=f"dash:{user.username}", scopes=frozenset({"admin"}))


@router.get("/agents/{slug}")
async def agent_detail(slug: str, user: DashUser = Depends(require_admin)) -> dict:
    """Perfil completo (com system prompt), chaves do agente (sem o segredo) e histórico de versões."""
    out = await agents.get_agent(_agent_admin(user), slug)
    out["agent"]["auto_apply_updates"] = bool((out["agent"].get("config") or {}).get("auto_apply_updates"))
    return out


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    system_prompt: str | None = Field(default=None, min_length=1)
    allowed_collections: list[str] | None = None
    scopes: list[str] | None = None
    auto_apply_updates: bool | None = None
    change_note: str | None = None


@router.patch("/agents/{slug}")
async def update_agent(slug: str, body: AgentPatch, user: DashUser = Depends(require_admin)) -> dict:
    """Edita o perfil do agente (gera uma versão nova). O slug não muda: a conexão MCP depende dele."""
    if body.scopes is not None and not body.scopes:
        raise KBError("O agente precisa de ao menos um escopo.")
    config = {"auto_apply_updates": body.auto_apply_updates} if body.auto_apply_updates is not None else None
    return await agents.update_agent(
        _agent_admin(user),
        slug,
        (body.change_note or "").strip() or _DASH_AGENT_NOTE,
        name=body.name.strip() if body.name else None,
        description=body.description.strip() if body.description is not None else None,
        system_prompt=body.system_prompt,
        config=config,
        allowed_collections=body.allowed_collections,
        scopes=body.scopes,
    )


class AgentArchiveIn(BaseModel):
    reason: str = "arquivado pela dashboard"


@router.post("/agents/{slug}/archive")
async def archive_agent(slug: str, body: AgentArchiveIn, user: DashUser = Depends(require_admin)) -> dict:
    """Arquiva o agente e revoga todas as chaves dele (o histórico fica para auditoria)."""
    return await agents.archive_agent(_agent_admin(user), slug, body.reason)


class AgentKeyIn(BaseModel):
    label: str | None = None


@router.post("/agents/{slug}/keys")
async def issue_agent_key(slug: str, body: AgentKeyIn, user: DashUser = Depends(require_admin)) -> dict:
    """Emite uma chave nova para o agente — o segredo e o comando de conexão aparecem só nesta resposta."""
    return await agents.issue_agent_key(_agent_admin(user), slug, (body.label or "").strip() or None)


# ---------------------------------------------------------------- chaves Bearer


@router.get("/keys")
async def list_keys(user: DashUser = Depends(current_user)) -> list[dict]:
    """Todas as chaves da API (prefixo, label, escopos, agente, último uso, status).

    O prefixo identifica a chave na revogação pela API/MCP: só admin o recebe."""
    rows = await pool().fetch(
        """
        SELECT k.id, k.prefix, k.label, k.scopes, k.revoked_at, k.last_used_at, k.created_at,
               a.slug AS agent
        FROM api_keys k
        LEFT JOIN agents a ON a.id = k.agent_id
        ORDER BY k.created_at DESC
        """
    )
    items = records(rows)
    if not user.is_admin:
        for item in items:
            item["prefix"] = None
    return items


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
            "UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL RETURNING prefix, agent_id",
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
        key = await create_api_key(conn, label=old["label"], scopes=list(old["scopes"]), agent_id=old["agent_id"])
        await audit(conn, f"dash:{user.username}", "dash.key.renew", old["prefix"], new_prefix=key["prefix"])
    return {"revoked_key_id": str(key_uuid), **key}


# ---------------------------------------------------------------- 2FA da própria conta


class PasswordIn(BaseModel):
    password: str


class CodeIn(BaseModel):
    code: str


class DisableMfaIn(BaseModel):
    password: str
    code: str


async def _recheck_password(user: DashUser, password: str, request: Request) -> None:
    """Mudanças no 2FA pedem a senha de novo (sessão roubada sozinha não liga/desliga nada).
    Erros contam no mesmo rate limit do login."""
    buckets = _login_buckets(user.username, request)
    _check_login_blocked(buckets)
    if await dash_auth.authenticate(user.username, password) is None:
        for limiter, key in buckets:
            limiter.register_failure(key)
        raise HTTPException(status_code=400, detail="Senha incorreta.")


@router.get("/account/2fa")
async def mfa_status(user: DashUser = Depends(current_user)) -> dict:
    return await mfa.status(user.id)


@router.post("/account/2fa/setup")
async def mfa_setup(body: PasswordIn, request: Request, user: DashUser = Depends(current_user)) -> dict:
    if await mfa.is_enabled(user.id):
        raise KBError("O 2FA já está ligado. Desligue antes de cadastrar outro aparelho.")
    await _recheck_password(user, body.password, request)
    return await mfa.begin_setup(user.id, user.username)


@router.post("/account/2fa/enable")
async def mfa_enable(body: CodeIn, request: Request, user: DashUser = Depends(current_user)) -> dict:
    codes = await mfa.confirm_setup(user.id, body.code)
    # sessões abertas antes do 2FA (ex.: de quem só tinha a senha) caem; esta continua
    await dash_auth.revoke_user_sessions(user.id, keep_token=request.cookies.get(dash_auth.SESSION_COOKIE))
    async with pool().acquire() as conn:
        await audit(conn, f"dash:{user.username}", "dash.2fa.enable", user.username)
    return {"recovery_codes": codes}


@router.post("/account/2fa/disable")
async def mfa_disable(body: DisableMfaIn, request: Request, user: DashUser = Depends(current_user)) -> dict:
    await _recheck_password(user, body.password, request)
    if await mfa.verify(user.id, body.code) is None:
        raise HTTPException(status_code=400, detail="Código inválido.")
    await mfa.disable(user.id)
    await dash_auth.revoke_user_sessions(user.id, keep_token=request.cookies.get(dash_auth.SESSION_COOKIE))
    async with pool().acquire() as conn:
        await audit(conn, f"dash:{user.username}", "dash.2fa.disable", user.username)
    return {"ok": True}


@router.post("/account/2fa/recovery-codes")
async def mfa_recovery_codes(body: CodeIn, user: DashUser = Depends(current_user)) -> dict:
    if await mfa.verify(user.id, body.code) is None:
        raise HTTPException(status_code=400, detail="Código inválido.")
    codes = await mfa.regenerate_recovery_codes(user.id)
    async with pool().acquire() as conn:
        await audit(conn, f"dash:{user.username}", "dash.2fa.recovery_regenerate", user.username)
    return {"recovery_codes": codes}


# ---------------------------------------------------------------- usuários da dash

USER_COLUMNS = "id, username, role, disabled_at, created_at, totp_enabled_at IS NOT NULL AS totp_enabled"


@router.get("/users")
async def list_users(_admin: DashUser = Depends(require_admin)) -> list[dict]:
    rows = await pool().fetch(f"SELECT {USER_COLUMNS} FROM dash_users ORDER BY username")
    return records(rows)


@router.delete("/users/{user_id}/2fa")
async def reset_user_mfa(user_id: str, admin: DashUser = Depends(require_admin)) -> dict:
    """Pessoa perdeu o celular e os recovery codes: o admin desliga o 2FA dela (e derruba as sessões)."""
    target = parse_uuid(user_id, "user_id")
    row = await pool().fetchrow("SELECT id, username FROM dash_users WHERE id = $1", target)
    if row is None:
        raise NotFound(f"Usuário {user_id} não encontrado.")
    await mfa.disable(str(row["id"]))
    await dash_auth.revoke_user_sessions(str(row["id"]))
    async with pool().acquire() as conn:
        await audit(conn, f"dash:{admin.username}", "dash.2fa.reset", row["username"])
    return {"ok": True}


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
async def update_dash_user(user_id: str, body: DashUserPatch, admin: DashUser = Depends(require_admin)) -> dict:
    target = parse_uuid(user_id, "user_id")
    if not (body.role is not None or body.disabled is not None or body.password is not None):
        raise KBError("Nada para alterar: informe role, disabled ou password.")
    row = await pool().fetchrow("SELECT id, username FROM dash_users WHERE id = $1", target)
    if row is None:
        raise NotFound(f"Usuário {user_id} não encontrado.")
    if str(row["id"]) == admin.id and ((body.role is not None and body.role != "admin") or body.disabled):
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
    await pool().execute(f"UPDATE dash_users SET {', '.join(sets)} WHERE id = ${len(args)}", *args)
    # desativação, troca de senha OU troca de papel derrubam as sessões ativas —
    # sem isso um admin rebaixado a viewer seguiria operando com o papel antigo
    if body.disabled or body.password is not None or body.role is not None:
        await dash_auth.revoke_user_sessions(str(row["id"]))
        await oauth.revoke_user_grants(str(row["id"]))  # e as conexões OAuth (Claude etc.)
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
    updated = record(await pool().fetchrow(f"SELECT {USER_COLUMNS} FROM dash_users WHERE id = $1", target))
    if updated is None:  # narrow pro mypy: a linha já foi validada antes do UPDATE
        raise NotFound(f"Usuário {user_id} não encontrado.")
    return updated


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


@router.get("/insights")
async def get_insights(days: int = Query(default=30, ge=1, le=365)) -> dict:
    """Tela Painel: indicadores, atividade no período e saúde da base — uma consulta só por bloco.

    O período (últimos `days` dias) vale para atividade, contribuidores e o delta de notas criadas
    (comparado com o período anterior de mesmo tamanho).
    """
    async with pool().acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT (SELECT count(*) FROM documents WHERE status = 'active') AS documents,
                   (SELECT count(*) FROM documents WHERE status = 'archived') AS archived,
                   (SELECT count(*) FROM collections) AS collections,
                   (SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id
                     WHERE d.status = 'active') AS chunks,
                   (SELECT count(*) FROM agents WHERE status = 'active') AS agents,
                   (SELECT count(*) FROM agent_memories) AS memories,
                   (SELECT count(*) FROM agent_versions WHERE status = 'proposed') AS proposals,
                   (SELECT count(*) FROM api_keys WHERE revoked_at IS NULL) AS active_keys,
                   (SELECT count(*) FROM dash_users WHERE disabled_at IS NULL) AS users,
                   (SELECT count(*) FROM document_links l
                      JOIN documents s ON s.id = l.source_id AND s.status = 'active'
                      JOIN documents t ON t.id = l.target_id AND t.status = 'active') AS links,
                   (SELECT count(*) FROM document_links WHERE target_id IS NULL) AS pending_links
            """
        )
        created = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE created_at >= now() - make_interval(days => $1)) AS current,
                   count(*) FILTER (WHERE created_at <  now() - make_interval(days => $1)
                                      AND created_at >= now() - make_interval(days => $1 * 2)) AS previous
            FROM documents WHERE status = 'active'
            """,
            days,
        )
        # uma linha por dia do período, inclusive os sem edição (zeros explícitos para o gráfico).
        # Agregação única por dia (GROUP BY) em vez de ~2×days subconsultas correlatas.
        edits = await conn.fetch(
            """
            WITH days AS (
                SELECT d::date AS day
                FROM generate_series(current_date - ($1 - 1), current_date, interval '1 day') AS d
            ),
            counts AS (
                SELECT created_at::date AS day,
                       count(*) AS edits,
                       count(*) FILTER (WHERE version = 1) AS created
                FROM document_versions
                WHERE created_at >= current_date - ($1 - 1)
                GROUP BY 1
            )
            SELECT days.day,
                   coalesce(counts.edits, 0) AS edits,
                   coalesce(counts.created, 0) AS created
            FROM days LEFT JOIN counts USING (day)
            ORDER BY day
            """,
            days,
        )
        by_collection = await conn.fetch(
            """
            SELECT c.name, count(d.id) FILTER (WHERE d.status = 'active') AS documents
            FROM collections c LEFT JOIN documents d ON d.collection_id = c.id
            GROUP BY c.name ORDER BY documents DESC, c.name
            """
        )
        contributors = await conn.fetch(
            """
            SELECT coalesce(changed_by, '—') AS actor, count(*) AS edits
            FROM document_versions
            WHERE created_at >= now() - make_interval(days => $1)
            GROUP BY 1 ORDER BY edits DESC, actor LIMIT 8
            """,
            days,
        )
        health = await conn.fetchrow(
            """
            SELECT count(*) AS documents,
                   count(*) FILTER (WHERE cardinality(d.tags) > 0) AS with_tags,
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM document_links l
                       WHERE (l.source_id = d.id AND l.target_id IS NOT NULL) OR l.target_id = d.id
                   )) AS with_links,
                   count(*) FILTER (WHERE d.updated_at < now() - interval '90 days') AS stale
            FROM documents d WHERE d.status = 'active'
            """
        )
        recent = await conn.fetch(
            """
            SELECT d.id, d.title, c.name AS collection, d.version, d.updated_at, d.updated_by
            FROM documents d JOIN collections c ON c.id = d.collection_id
            WHERE d.status = 'active' ORDER BY d.updated_at DESC LIMIT 8
            """
        )
    return {
        "days": days,
        "totals": records([totals])[0],
        "created": records([created])[0],
        "activity": records(edits),
        "by_collection": records(by_collection),
        "contributors": records(contributors),
        "health": records([health])[0],
        "recent": records(recent),
    }


class SearchTestIn(BaseModel):
    query: str
    collections: list[str] | None = None
    folder_id: str | None = None  # só a pasta e as subpastas
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/search-test")
async def search_test(body: SearchTestIn, user: DashUser = Depends(current_user)) -> dict:
    """Roda a busca híbrida real da base e devolve os chunks, para destacar no grafo."""
    principal = Principal(actor=f"dash:{user.username}", scopes=frozenset({"read"}))
    folder_ids = None
    if body.folder_id:
        async with pool().acquire() as conn:
            folder_ids = await folders.folder_subtree_ids(conn, principal, body.folder_id)
    results = await search.search_knowledge(
        principal, body.query, body.collections, None, None, body.top_k, folder_ids=folder_ids
    )
    return {"results": results}
