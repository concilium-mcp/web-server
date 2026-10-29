"""Endurecimento pós-auditoria (plan-web-31) — sem banco."""

import pytest
from starlette.requests import Request

from mcp_rag_api.config import check_config, get_settings
from mcp_rag_api.dashboard_api import client_ip
from mcp_rag_api.main import SECURITY_HEADERS


@pytest.fixture
def settings_env(monkeypatch):
    """Settings lido do ambiente do teste, com o lru_cache limpo antes e depois."""
    get_settings.cache_clear()
    try:
        yield monkeypatch
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("bind", ["0.0.0.0", "10.0.0.5", "::"])
def test_auth_disabled_refused_when_bound_outside_loopback(monkeypatch, settings_env, bind):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("KB_AUTH_DISABLED", "true")
    monkeypatch.setenv("KB_BIND_HOST", bind)  # PUBLIC_URL segue local: só o bind denuncia a exposição
    with pytest.raises(RuntimeError, match="loopback"):
        check_config()


@pytest.mark.parametrize("bind", ["127.0.0.1", "localhost", ""])
def test_auth_disabled_allowed_on_loopback_or_unknown_bind(monkeypatch, settings_env, bind):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("KB_AUTH_DISABLED", "true")
    monkeypatch.setenv("KB_BIND_HOST", bind)
    assert any("KB_AUTH_DISABLED" in w for w in check_config())


def test_public_bind_is_fine_with_auth_enabled(monkeypatch, settings_env):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("KB_BIND_HOST", "0.0.0.0")
    check_config()


def _request(headers: dict[str, str], peer: str = "172.18.0.4") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234)})


def test_client_ip_uses_cf_header_only_when_trusted(monkeypatch, settings_env):
    req = _request({"CF-Connecting-IP": "203.0.113.7"})
    assert client_ip(req) == "203.0.113.7"
    monkeypatch.setenv("TRUST_CF_CONNECTING_IP", "false")
    get_settings.cache_clear()
    assert client_ip(req) == "172.18.0.4"


def test_csp_blocks_offsite_forms_base_and_framing():
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    for directive in ("form-action 'self'", "base-uri 'self'", "object-src 'none'", "frame-ancestors 'none'"):
        assert directive in csp
