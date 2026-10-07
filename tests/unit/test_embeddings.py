"""Retry/backoff dos provedores voyage/openai, shutdown de clients e modelo local assíncrono."""

import logging
import sys
import types

import httpx
import numpy as np
import pytest

from mcp_rag_api.config import EMBEDDING_DIM, get_settings
from mcp_rag_api.core import embeddings


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")


def _ok_payload() -> dict:
    return {"data": [{"index": 0, "embedding": [0.5] * EMBEDDING_DIM}]}


async def test_retry_recupera_apos_429(monkeypatch):
    monkeypatch.setattr(embeddings, "RETRY_BASE_DELAY", 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate_limit"})
        return httpx.Response(200, json=_ok_payload())

    provider = embeddings.VoyageProvider("key", "voyage-3.5")
    provider.client = _mock_client(handler)
    vectors = await provider.embed(["olá"], "query")
    assert calls["n"] == 2
    assert vectors[0].shape == (EMBEDDING_DIM,)
    await provider.aclose()


async def test_retry_esgota_tentativas_em_5xx(monkeypatch, caplog):
    monkeypatch.setattr(embeddings, "RETRY_BASE_DELAY", 0.0)
    monkeypatch.setattr(logging.getLogger("mcp_rag_api"), "propagate", True)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={})

    provider = embeddings.VoyageProvider("key", "voyage-3.5")
    provider.client = _mock_client(handler)
    with pytest.raises(embeddings.EmbeddingProviderError, match="voyage"):
        await provider.embed(["x"])
    assert calls["n"] == embeddings.MAX_ATTEMPTS
    await provider.aclose()
    assert any("provider=voyage" in r.getMessage() for r in caplog.records)


async def test_timeout_tambem_repete(monkeypatch):
    monkeypatch.setattr(embeddings, "RETRY_BASE_DELAY", 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("estourou", request=request)

    provider = embeddings.VoyageProvider("key", "voyage-3.5")
    provider.client = _mock_client(handler)
    with pytest.raises(embeddings.EmbeddingProviderError, match="timeout"):
        await provider.embed(["x"])
    assert calls["n"] == embeddings.MAX_ATTEMPTS
    await provider.aclose()


async def test_erro_4xx_nao_repete(monkeypatch):
    monkeypatch.setattr(embeddings, "RETRY_BASE_DELAY", 0.0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={})

    provider = embeddings.OpenAIProvider("key", "text-embedding-3-small")
    provider.client = _mock_client(handler)
    with pytest.raises(embeddings.EmbeddingProviderError, match="401"):
        await provider.embed(["x"])
    assert calls["n"] == 1
    await provider.aclose()


async def test_close_embedder_fecha_client_e_limpa_cache(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    get_settings.cache_clear()
    embeddings.get_embedder.cache_clear()
    try:
        provider = embeddings.get_embedder()
        assert isinstance(provider, embeddings.VoyageProvider)
        assert embeddings.get_embedder.cache_info().currsize == 1
        await embeddings.close_embedder()
        assert embeddings.get_embedder.cache_info().currsize == 0
        assert provider.client.is_closed
    finally:
        get_settings.cache_clear()
        embeddings.get_embedder.cache_clear()


async def test_local_provider_carrega_modelo_na_primeira_chamada(monkeypatch):
    class _FakeST:
        def __init__(self, name: str):
            self.name = name

        def encode(self, texts, normalize_embeddings=True):
            return np.ones((len(texts), EMBEDDING_DIM), dtype="float32")

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    provider = embeddings.LocalProvider("modelo-x")
    assert provider.model is None  # não carrega de forma síncrona no __init__
    await provider.warmup()
    assert provider.model is not None
    vectors = await provider.embed(["oi"])
    assert vectors[0].shape == (EMBEDDING_DIM,)


def test_local_provider_exige_extra_local():
    if "sentence_transformers" in sys.modules:
        pytest.skip("extra 'local' disponível no ambiente")
    with pytest.raises(RuntimeError, match="extra 'local'"):
        embeddings.LocalProvider("BAAI/bge-m3")
