"""Rotas HTTP do OAuth do MCP (plan-web-17).

- Protocolo (metadata, /authorize, /token, /register, /revoke): rotas do SDK, montadas em main.py.
- Tela "Autorizar" (/oauth/consent) + a API que ela chama (/oauth/api/requests/...): sessão da dash.
- Conexões na dash (/dash/api/oauth/grants): listar e revogar.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.routing import Route

from .config import get_settings
from .core import dash_auth, oauth
from .core.dash_auth import DashUser
from .dashboard_api import current_user, require_csrf_header

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def optional_user(request: Request) -> DashUser | None:
    token = request.cookies.get(dash_auth.SESSION_COOKIE)
    return await dash_auth.resolve_session(token) if token else None


consent_router = APIRouter(tags=["oauth"], dependencies=[Depends(require_csrf_header)])


@consent_router.get(oauth.CONSENT_PATH, include_in_schema=False)
async def consent_page() -> FileResponse:
    # página estática: o JS lê ?request= e fala com /oauth/api (sem script inline — CSP)
    return FileResponse(STATIC_DIR / "oauth" / "consent.html", headers={"Cache-Control": "no-store"})


@consent_router.get("/oauth/api/requests/{request_id}", include_in_schema=False)
async def consent_request(request_id: str, user: DashUser | None = Depends(optional_user)) -> dict:
    return await oauth.consent_info(request_id, user)


class DecisionIn(BaseModel):
    approve: bool
    scopes: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)  # vazio = todas
    agent_slug: str | None = None


@consent_router.post("/oauth/api/requests/{request_id}/decision", include_in_schema=False)
async def consent_decision(request_id: str, body: DecisionIn, user: DashUser = Depends(current_user)) -> dict:
    redirect = await oauth.decide(
        request_id,
        user,
        approve=body.approve,
        scopes=body.scopes,
        collections=body.collections,
        agent_slug=body.agent_slug,
    )
    return {"redirect": redirect}


grants_router = APIRouter(
    prefix="/dash/api/oauth",
    tags=["dashboard"],
    dependencies=[Depends(require_csrf_header), Depends(current_user)],
)


@grants_router.get("/grants")
async def list_grants(
    all_users: bool = Query(default=False, alias="all"), user: DashUser = Depends(current_user)
) -> list[dict]:
    """Conexões OAuth ativas (apps autorizados). `all=true`: de todos os usuários (admin)."""
    return await oauth.list_grants(user, all_users=all_users)


@grants_router.post("/grants/{grant_id}/revoke")
async def revoke_grant(grant_id: str, user: DashUser = Depends(current_user)) -> dict:
    await oauth.revoke_grant(grant_id, user)
    return {"ok": True}


def protocol_routes() -> list[Route]:
    """Rotas do SDK: metadata do authorization server (RFC 8414) e do recurso (RFC 9728) + endpoints."""
    from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

    # AuthSettings preserva a URL sem barra no fim: o issuer do metadata tem que bater exato (RFC 8414)
    urls = AuthSettings.model_validate(
        {
            "issuer_url": get_settings().public_url.rstrip("/"),
            "resource_server_url": oauth.resource_url(),
            "validate_token_resource": False,
        }
    )
    issuer, resource = urls.issuer_url, urls.resource_server_url
    assert resource is not None
    routes = create_auth_routes(
        oauth.provider,
        issuer_url=issuer,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes += create_protected_resource_routes(
        resource_url=resource,
        authorization_servers=[issuer],
        scopes_supported=list(oauth.OAUTH_SCOPES),
        resource_name="Concilium",
    )
    return routes
