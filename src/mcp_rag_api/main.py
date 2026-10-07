"""App HTTP: API REST (FastAPI) + endpoint MCP streamable HTTP em /mcp + dashboard web em /dashboard."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from . import db
from .api import router
from .config import get_settings
from .dashboard_api import auth_router
from .dashboard_api import router as dashboard_router
from .mcp_server import mcp
from .security import KBError, NotFound, PermissionDenied, VersionConflict, bearer_token, resolve_key

# Precisa ser criado antes do lifespan: é aqui que o MCPServer instancia o session manager.
# host="0.0.0.0" evita a proteção automática que só aceita Host localhost (atrás de proxy/domínio).
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, host="0.0.0.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with db.lifespan(), mcp.session_manager.run():
        yield


app = FastAPI(title="MCP RAG API", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(auth_router)
app.include_router(dashboard_router)


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
                        await resolve_key(conn, token)
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
async def dashboard_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Montado por último: as rotas REST têm prioridade; /mcp cai no app do MCP.
app.mount("/", RequireApiKey(mcp_app))
