"""Provider huggingface: lotes, retry em 429/503/timeout e validação da resposta (sem rede: MockTransport)."""

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


async def test_retries_on_timeout_then_succeeds():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ReadTimeout("lento", request=request)
        return httpx.Response(200, json=[_vec(1)])

    p = HuggingFaceProvider("hf_x", "", transport=httpx.MockTransport(handler))
    assert (await p.embed_one("olá")).shape == (EMBEDDING_DIM,)
    assert len(attempts) == 3


async def test_timeout_on_every_attempt_is_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    p = HuggingFaceProvider("hf_x", "", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        await p.embed_one("olá")


def test_default_batch_is_small():
    assert HuggingFaceProvider("hf_x", "").batch_size == 8


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


def _counting(status: int, calls: list, name: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((name, request.headers["authorization"], str(request.url)))
        return httpx.Response(status, json=[_vec(1)] if status == 200 else {"error": "down"})

    return httpx.MockTransport(handler)


def _with_fallback(primary: httpx.MockTransport, fallback: httpx.MockTransport) -> HuggingFaceProvider:
    return HuggingFaceProvider(
        "own_key",
        "",
        api_url="http://own:8080/embed",
        transport=primary,
        fallback_url="https://hf.test/embed",
        fallback_key="hf_key",
        fallback_transport=fallback,
    )


async def test_primary_answers_and_fallback_stays_idle():
    calls: list = []
    p = _with_fallback(_counting(200, calls, "own"), _counting(200, calls, "hf"))
    assert (await p.embed_one("olá")).shape == (EMBEDDING_DIM,)
    assert calls == [("own", "Bearer own_key", "http://own:8080/embed")]


async def test_falls_back_when_primary_is_down_and_rests_it():
    calls: list = []
    p = _with_fallback(_counting(500, calls, "own"), _counting(200, calls, "hf"))
    assert (await p.embed_one("olá")).shape == (EMBEDDING_DIM,)
    assert [c[0] for c in calls] == ["own", "hf"] and calls[1][1:] == ("Bearer hf_key", "https://hf.test/embed")
    # Enquanto descansa, a principal nem é chamada: a busca não paga a espera dela de novo.
    await p.embed_one("outra")
    assert [c[0] for c in calls] == ["own", "hf", "hf"]
    # Passado o descanso, a principal volta a ser tentada.
    assert p._primary_back_at > embeddings.time.monotonic()
    p._primary_back_at = 0.0  # sem mexer no relógio do processo: o laço assíncrono depende dele
    await p.embed_one("mais uma")
    assert [c[0] for c in calls][-2:] == ["own", "hf"]


async def test_falls_back_on_timeout_with_few_attempts():
    attempts: list = []
    calls: list = []

    def slow(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectTimeout("fora", request=request)

    p = _with_fallback(httpx.MockTransport(slow), _counting(200, calls, "hf"))
    assert (await p.embed_one("olá")).shape == (EMBEDDING_DIM,)
    assert len(attempts) == p.PRIMARY_ATTEMPTS and len(calls) == 1


async def test_both_down_raises():
    calls: list = []
    p = _with_fallback(_counting(500, calls, "own"), _counting(503, calls, "hf"))
    with pytest.raises(httpx.HTTPStatusError):
        await p.embed_one("olá")


def test_fallback_requires_its_key():
    with pytest.raises(RuntimeError, match="EMBEDDING_FALLBACK_KEY"):
        HuggingFaceProvider("own_key", "", api_url="http://own/embed", fallback_url="https://hf.test/embed")
