"""Allowlist de redirect_uri do registro dinâmico do OAuth (clientes MCP: Claude, ChatGPT, Cursor, VS Code)."""

import pytest

from mcp_rag_api.config import get_settings
from mcp_rag_api.core.oauth import redirect_allowed, redirect_label


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv("OAUTH_REDIRECT_ALLOWLIST", raising=False)
    monkeypatch.delenv("OAUTH_REDIRECT_SCHEMES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
        "https://chatgpt.com/connector/oauth/7DoEaYwjkfwH",
        "https://platform.openai.com/oauth/callback",
        "https://vscode.dev/redirect",
        "https://chat.mistral.ai/mcp/callback",
        "http://localhost:33418/callback",
        "http://127.0.0.1:6274/oauth/callback",
        "cursor://anysphere.cursor-mcp/oauth/callback",
        "vscode://vscode.github-authentication/did-authenticate",
    ],
)
def test_known_clients_allowed(uri):
    assert redirect_allowed(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/cb",
        "https://claude.ai.evil.example/cb",
        "https://notchatgpt.com/cb",
        "http://chatgpt.com/cb",  # http só em loopback
        "http://192.168.0.10/cb",
        "evilapp://callback",
        "javascript://alert(1)",
        "",
    ],
)
def test_unknown_redirects_refused(uri):
    assert not redirect_allowed(uri)


def test_env_extends_allowlist(monkeypatch):
    monkeypatch.setenv("OAUTH_REDIRECT_ALLOWLIST", "minha-empresa.com")
    monkeypatch.setenv("OAUTH_REDIRECT_SCHEMES", "meuapp,https")
    get_settings.cache_clear()
    assert redirect_allowed("https://app.minha-empresa.com/cb")
    assert redirect_allowed("meuapp://callback")
    assert not redirect_allowed("https://claude.ai/api/mcp/auth_callback")  # a lista do env substitui a padrão
    assert not redirect_allowed("https://evil.example/cb")  # "https" na lista de esquemas é ignorado


def test_redirect_label():
    assert redirect_label("https://chatgpt.com/connector/oauth/x") == "chatgpt.com"
    assert redirect_label("cursor://anysphere.cursor-mcp/oauth/callback") == "cursor://anysphere.cursor-mcp"
