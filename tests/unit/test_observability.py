"""Observabilidade HTTP: handler de 500 e resumo de boot (sem segredos)."""

import logging

from starlette.testclient import TestClient

from mcp_rag_api import db
from mcp_rag_api.config import Settings
from mcp_rag_api.main import _log_boot_summary, app


class _FakePool:
    async def fetchval(self, _query: str) -> int:
        return 1


async def test_health_disponivel_sem_auth(monkeypatch):
    monkeypatch.setattr(db, "pool", lambda: _FakePool())
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200


def test_500_devolve_json_generico_e_loga(monkeypatch, caplog):
    """Handler de Exception responde 500 genérico e loga o erro (sem vazar detalhes)."""

    def _boom():
        raise RuntimeError("falha inesperada")

    monkeypatch.setattr(db, "pool", _boom)
    monkeypatch.setattr(logging.getLogger("mcp_rag_api"), "propagate", True)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 500
    assert "falha inesperada" not in str(resp.json())
    assert any("erro 500" in r.getMessage() for r in caplog.records)


def test_boot_summary_loga_config_sem_segredos(monkeypatch, caplog):
    fake_settings = Settings(voyage_api_key="segredo-super-secreto", embedding_provider="fake")
    monkeypatch.setattr("mcp_rag_api.main.get_settings", lambda: fake_settings)
    monkeypatch.setattr(logging.getLogger("mcp_rag_api"), "propagate", True)
    with caplog.at_level(logging.INFO, logger="mcp_rag_api"):
        _log_boot_summary()
    mensagens = [r.getMessage() for r in caplog.records]
    assert any("embedding_provider=fake" in m for m in mensagens)
    assert any("dash_session_ttl=168h" in m for m in mensagens)
    assert not any("segredo-super-secreto" in m for m in mensagens)
