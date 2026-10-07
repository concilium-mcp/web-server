"""Eventos de auth da dash logados (login ok/falha, logout) — nunca com senha."""

import logging
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from mcp_rag_api.config import Settings
from mcp_rag_api.core import dash_auth
from mcp_rag_api.main import app

# O portão CSRF da dash (web-04) exige o header que o dashboard.js envia em todo fetch.
CSRF = {"x-requested-with": "fetch"}


def _propaga_logs(monkeypatch):
    monkeypatch.setattr(logging.getLogger("mcp_rag_api"), "propagate", True)


def test_login_ok_loga_sem_senha(monkeypatch, caplog):
    _propaga_logs(monkeypatch)
    user = dash_auth.DashUser(id="u1", username="maria", role="admin")
    monkeypatch.setattr(dash_auth, "authenticate", AsyncMock(return_value=user))
    monkeypatch.setattr(dash_auth, "create_session", AsyncMock(return_value="token"))
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="mcp_rag_api"):
        resp = client.post(
            "/dash/api/auth/login", json={"username": "maria", "password": "senha-secreta"}, headers=CSRF
        )
    assert resp.status_code == 200
    mensagens = [r.getMessage() for r in caplog.records]
    assert any("login ok" in m and "maria" in m for m in mensagens)
    assert not any("senha-secreta" in m for m in mensagens)


def test_login_falho_loga_sem_senha(monkeypatch, caplog):
    _propaga_logs(monkeypatch)
    monkeypatch.setattr(dash_auth, "authenticate", AsyncMock(return_value=None))
    client = TestClient(app)
    resp = client.post(
        "/dash/api/auth/login", json={"username": "maria", "password": "senha-secreta"}, headers=CSRF
    )
    assert resp.status_code == 401
    mensagens = [r.getMessage() for r in caplog.records]
    assert any("login falhou" in m and "maria" in m for m in mensagens)
    assert not any("senha-secreta" in m for m in mensagens)


def test_logout_loga_username(monkeypatch, caplog):
    _propaga_logs(monkeypatch)
    user = dash_auth.DashUser(id="u1", username="maria", role="admin")
    monkeypatch.setattr(dash_auth, "resolve_session", AsyncMock(return_value=user))
    monkeypatch.setattr(dash_auth, "revoke_session", AsyncMock(return_value=None))
    client = TestClient(app)
    client.cookies.set(dash_auth.SESSION_COOKIE, "token")
    with caplog.at_level(logging.INFO, logger="mcp_rag_api"):
        resp = client.post("/dash/api/auth/logout", headers=CSRF)
    assert resp.status_code == 200
    mensagens = [r.getMessage() for r in caplog.records]
    assert any("logout" in m and "maria" in m for m in mensagens)


@pytest.mark.parametrize("valor,esperado", [(1, 3600), (24, 86400), (168, 604800)])
def test_cookie_max_age_segue_ttl_configurado(monkeypatch, valor, esperado):
    user = dash_auth.DashUser(id="u1", username="maria", role="admin")
    monkeypatch.setattr(dash_auth, "authenticate", AsyncMock(return_value=user))
    monkeypatch.setattr(dash_auth, "create_session", AsyncMock(return_value="token"))
    fake_settings = Settings(dash_session_ttl_hours=valor)
    monkeypatch.setattr(dash_auth, "get_settings", lambda: fake_settings)
    client = TestClient(app)
    resp = client.post("/dash/api/auth/login", json={"username": "maria", "password": "x"}, headers=CSRF)
    assert resp.status_code == 200
    set_cookie = resp.headers["set-cookie"]
    assert f"Max-Age={esperado}" in set_cookie
