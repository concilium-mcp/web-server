"""/dash/docs (Swagger) compatível com a CSP da dashboard: nada de CDN nem <script> inline."""

import re

import httpx

from mcp_rag_api.core import dash_auth
from mcp_rag_api.main import app


async def test_docs_page_respects_csp():
    await dash_auth.create_user("dd-admin", "senha-segura-123", "admin")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dash.test", headers={"X-Requested-With": "fetch"}
    ) as client:
        assert (await client.get("/dash/docs")).status_code == 401  # sem sessão
        await client.post("/dash/api/auth/login", json={"username": "dd-admin", "password": "senha-segura-123"})
        resp = await client.get("/dash/docs")
        assert resp.status_code == 200
        html = resp.text
        assert "script-src 'self'" in resp.headers["content-security-policy"]
        scripts = re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)
        assert scripts and all('src="/static/' in attrs and not body.strip() for attrs, body in scripts)
        assert "cdn." not in html and "http" not in re.sub(r"<!doctype[^>]*>", "", html, flags=re.I)
        for path in ("/static/vendor/swagger-ui/swagger-ui-bundle.js", "/static/js/swagger-init.js"):
            assert (await client.get(path)).status_code == 200
        assert (await client.get("/dash/api/openapi.json")).json()["paths"]
