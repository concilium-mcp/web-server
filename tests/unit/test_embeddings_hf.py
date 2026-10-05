"""Provider huggingface: lotes, retry em 429/503 e validação da resposta (sem rede: MockTransport)."""

import httpx
import numpy as np
import pytest

from mcp_rag_api.config import EMBEDDING_DIM
from mcp_rag_api.core import embeddings
from mcp_rag_api.core.embeddings import HuggingFaceProvider


def _vec(seed: int) -> list[float]:
    v = np.random.default_rng(seed).normal(size=EMBEDDING_DIM)
    return (v / np.linalg.norm(v)).tolist()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(embeddings.asyncio, "sleep", instant)


async def test_batches_and_auth_header():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        calls.append((request.headers["authorization"], body))
        n = body.count('"t')  # textos t0, t1, ...
        return httpx.Response(200, json=[_vec(i) for i in range(n)])

    p = HuggingFaceProvider("hf_x", "", batch_size=2, transport=httpx.MockTransport(handler))
    out = await p.embed([f"t{i}" for i in range(5)])
    assert len(out) == 5 and out[0].shape == (EMBEDDING_DIM,)
    assert len(calls) == 3  # 2 + 2 + 1
    assert calls[0][0] == "Bearer hf_x"
    assert p.url.endswith("/models/BAAI/bge-m3/pipeline/feature-extraction")


async def test_retries_on_model_loading_then_succeeds():
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(status, json=[_vec(1)] if status == 200 else {"error": "loading"})

    p = HuggingFaceProvider("hf_x", "", transport=httpx.MockTransport(handler))
    vec = await p.embed_one("olá")
    assert vec.shape == (EMBEDDING_DIM,)


async def test_gives_up_after_retries():
    p = HuggingFaceProvider("hf_x", "", transport=httpx.MockTransport(lambda r: httpx.Response(503, json={})))
    with pytest.raises(httpx.HTTPStatusError):
        await p.embed_one("olá")


def _fixed(json) -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200, json=json))


async def test_rejects_wrong_dimension_and_count():
    wrong_dim = HuggingFaceProvider("hf_x", "", transport=_fixed([[0.1] * 384]))  # modelo de 384 dims
    with pytest.raises(ValueError):
        await wrong_dim.embed_one("olá")
    wrong_count = HuggingFaceProvider("hf_x", "", transport=_fixed([]))
    with pytest.raises(ValueError):
        await wrong_count.embed_one("olá")


def test_requires_api_key():
    with pytest.raises(RuntimeError, match="HF_API_KEY"):
        HuggingFaceProvider("", "")
