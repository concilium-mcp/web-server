"""redirect_uri aceitos no registro dinâmico do OAuth (clientes MCP: Claude, ChatGPT, Cursor, VS Code).

Desde o plan-web-32: endereços exatos (ou caminho fixo + "*"), sem subdomínios.
"""

import pytest

from mcp_rag_api.config import check_config, get_settings
from mcp_rag_api.core.oauth import redirect_allowed, redirect_label


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for var in ("OAUTH_REDIRECT_URIS", "OAUTH_REDIRECT_ALLOWLIST", "OAUTH_REDIRECT_SCHEMES"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
        "https://chatgpt.com/connector/oauth/7DoEaYwjkfwH",
        "https://chatgpt.com/connector_platform_oauth_redirect",
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
        # subdomínio ou outro caminho do mesmo site: podem ter conteúdo de terceiros
        "https://evil.claude.ai/api/mcp/auth_callback",
        "https://artifacts.claude.ai/api/mcp/auth_callback",
        "https://claude.ai/outra/pagina",
        "https://claude.ai/api/mcp/auth_callback/extra",
        "https://chatgpt.com/share/abc",
        "https://chatgpt.com/connector/oauthX",
        "https://openai.com/oauth/callback",
        # truques para sair do caminho fixo ou confundir o host
        "https://chatgpt.com/connector/oauth/../../share/x",
        "https://chatgpt.com/connector/oauth/%2e%2e/%2e%2e/share/x",
        "https://chatgpt.com/connector/oauth/a%2F..%2Fb",
        "https://attacker@claude.ai/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback#frag",
        "https://claude.ai/api/mcp/auth_callback?next=https://evil.example",
        "https://claude.ai:8443/api/mcp/auth_callback",
        "https://claude.ai\\@evil.example/api/mcp/auth_callback",
    ],
)
def test_unknown_redirects_refused(uri):
    assert not redirect_allowed(uri)


def test_env_replaces_uris_and_schemes(monkeypatch):
    monkeypatch.setenv("OAUTH_REDIRECT_URIS", "https://app.minha-empresa.com/cb, https://sso.minha-empresa.com/r/*")
    monkeypatch.setenv("OAUTH_REDIRECT_SCHEMES", "meuapp,https")
    get_settings.cache_clear()
    assert redirect_allowed("https://app.minha-empresa.com/cb")
    assert redirect_allowed("https://sso.minha-empresa.com/r/123")
    assert not redirect_allowed("https://x.app.minha-empresa.com/cb")  # sem subdomínios
    assert redirect_allowed("meuapp://callback")
    assert not redirect_allowed("https://claude.ai/api/mcp/auth_callback")  # a lista do env substitui a padrão
    assert not redirect_allowed("https://evil.example/cb")  # "https" na lista de esquemas é ignorado


def test_old_host_allowlist_is_ignored_and_warned(monkeypatch):
    monkeypatch.setenv("OAUTH_REDIRECT_ALLOWLIST", "claude.ai,evil.example")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    get_settings.cache_clear()
    assert not redirect_allowed("https://evil.example/cb")
    assert not redirect_allowed("https://x.claude.ai/cb")
    assert any("OAUTH_REDIRECT_ALLOWLIST" in w for w in check_config())


def test_redirect_label():
    assert redirect_label("https://chatgpt.com/connector/oauth/x") == "chatgpt.com"
    assert redirect_label("cursor://anysphere.cursor-mcp/oauth/callback") == "cursor://anysphere.cursor-mcp"
