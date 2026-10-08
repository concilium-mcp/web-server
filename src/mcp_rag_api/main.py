"""App HTTP: API REST (FastAPI) + endpoint MCP streamable HTTP em /mcp + dashboard web em /dashboard."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from . import db
from .api import router
from .config import EMBEDDING_DIM, check_config, get_settings
from .core.dash_auth import DashUser
from .core.embeddings import close_embedder, get_embedder
from .dashboard_api import auth_router, current_user
from .dashboard_api import router as dashboard_router
from .logging_config import get_logger, setup_logging
from .mcp_server import mcp
from .security import KBError, NotFound, PermissionDenied, VersionConflict, bearer_token, resolve_key

logger = get_logger()

# Precisa ser criado antes do lifespan: é aqui que o MCPServer instancia o session manager.
# host="0.0.0.0" evita a proteção automática que só aceita Host localhost (atrás de proxy/domínio).
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, host="0.0.0.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _static_version() -> str | None:
    """Versão dos estáticos: hash de conteúdo gravado no build da imagem (Dockerfile.coolify).

    Em dev (sem o arquivo) a dash roda sem URL versionada e tudo fica no-cache.
    """
    try:
        return (Path(__file__).resolve().parent / "static_version.txt").read_text().strip() or None
    except OSError:
        return None


APP_VERSION = _static_version()
VERSIONED_STATIC_PREFIX = f"/static/{APP_VERSION}" if APP_VERSION else None


def _log_boot_summary() -> None:
    """Resumo de config no boot: provider, dim, auth, TTL da dash — nunca segredos."""
    s = get_settings()
    auth_mode = "DESABILITADA" if s.kb_auth_disabled else "api_keys (bearer)"
    logger.info(
        "boot: embedding_provider=%s embedding_dim=%d auth=%s dash_session_ttl=%dh public_url=%s",
        s.embedding_provider,
        EMBEDDING_DIM,
        auth_mode,
        s.dash_session_ttl_hours,
        s.public_url,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    _log_boot_summary()
    for warning in check_config():
        logger.warning("%s", warning)
    if get_settings().embedding_provider == "local":
        # Pré-carrega o modelo (~2 GB) fora do caminho das requisições.
        await get_embedder().warmup()
    try:
        async with db.lifespan(), mcp.session_manager.run():
            yield
    finally:
        await close_embedder()


app = FastAPI(
    title="MCP RAG API",
    version="0.1.0",
    lifespan=lifespan,
    # Sem /docs público: o Swagger fica em /dash/docs, atrás da sessão da dashboard.
    docs_url=None,
    openapi_url=None,
)
app.include_router(router)
app.include_router(auth_router)
app.include_router(dashboard_router)

# Headers de segurança em toda resposta. A CSP assume que a dash carrega tudo de
# self/vendored (scripts e estilos locais; o Toast UI injeta <style> inline, por isso
# style-src leva 'unsafe-inline'); não há fonte externa nem asset remoto.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "connect-src 'self'"
    ),
}


@app.middleware("http")
async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    path = request.url.path
    if VERSIONED_STATIC_PREFIX and path.startswith(VERSIONED_STATIC_PREFIX + "/"):
        # URL com hash de conteúdo: muda a cada build que toca os estáticos, então pode
        # cachear para sempre (navegador e CDN). Deploy novo = URL nova = sem stale.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/dashboard" or path.startswith("/static/"):
        # HTML e estáticos sem versão (dev, ícones via fetch relativo): no-cache força
        # revalidação por ETag (304 barato quando nada mudou).
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(KBError)
async def kb_error_handler(_request: Request, exc: KBError) -> JSONResponse:
    status = 404 if isinstance(exc, NotFound) else 403 if isinstance(exc, PermissionDenied) else 400
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(VersionConflict)
async def version_conflict_handler(_request: Request, exc: VersionConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": {"message": str(exc), "current_version": exc.current_version}},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Loga 500s não tratados e responde JSON genérico (sem expor detalhes internos)."""
    logger.exception("erro 500 não tratado em %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor."})


class RequireApiKey:
    """Barra o /mcp inteiro (inclusive initialize e list_tools) sem uma chave válida.

    Cada tool ainda resolve a chave e confere escopos; isto é só o portão, para que um servidor
    exposto na internet não revele nem a lista de tools a quem não tem chave.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not get_settings().kb_auth_disabled:
            headers = dict(scope["headers"])
            token = bearer_token(headers.get(b"authorization", b"").decode())
            valid = False
            if token:
                try:
                    async with db.pool().acquire() as conn:
                        # Guarda o Principal no scope para as tools do /mcp reutilizarem (evita resolver
                        # a mesma chave 2× por request). Não é cache entre requests: cada request
                        # re-resolve no portão, então revogação continua imediata.
                        scope["kb.principal"] = await resolve_key(conn, token)
                    valid = True
                except PermissionDenied:
                    pass
            if not valid:
                response = JSONResponse(
                    {"detail": "Chave de API ausente, inválida ou revogada."},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


@app.get("/dashboard", include_in_schema=False)
async def dashboard_page() -> Response:
    if VERSIONED_STATIC_PREFIX is None:
        return FileResponse(STATIC_DIR / "dashboard.html")
    # Reescreve as referências do HTML para as URLs versionadas (imutáveis). O HTML em si
    # segue no-cache: cada navegação busca o HTML novo, que aponta para os assets do build atual.
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    html = html.replace('src="static/', f'src="{VERSIONED_STATIC_PREFIX}/').replace(
        'href="static/', f'href="{VERSIONED_STATIC_PREFIX}/'
    )
    return HTMLResponse(html)


@app.get("/dash/docs", include_in_schema=False)
async def dash_docs(_user: DashUser = Depends(current_user)) -> HTMLResponse:
    """Swagger UI da API REST: só quem está logado na dashboard vê."""
    return get_swagger_ui_html(openapi_url="/dash/api/openapi.json", title=f"{app.title} — documentação")


@app.get("/dash/api/openapi.json", include_in_schema=False)
async def dash_openapi(_user: DashUser = Depends(current_user)) -> JSONResponse:
    """OpenAPI da API REST, protegido pela mesma sessão da dashboard."""
    return JSONResponse(get_openapi(title=app.title, version=app.version, routes=app.routes))


if VERSIONED_STATIC_PREFIX is not None:
    # Montado antes do /static sem versão: /static/<hash>/... tem precedência por ser mais específico.
    app.mount(VERSIONED_STATIC_PREFIX, StaticFiles(directory=STATIC_DIR), name="static-versioned")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Montado por último: as rotas REST têm prioridade; /mcp cai no app do MCP.
app.mount("/", RequireApiKey(mcp_app))
