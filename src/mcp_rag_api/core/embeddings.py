"""Provedores de embeddings plugáveis. Todos devolvem vetores de EMBEDDING_DIM dimensões."""

import asyncio
import hashlib
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any, Literal

import httpx
import numpy as np

from ..config import EMBEDDING_DIM, get_settings
from ..logging_config import get_logger

InputType = Literal["document", "query"]

# Retry de chamadas ao provedor: só para falhas transitórias (429, 5xx, timeout, transporte),
# com backoff exponencial. Falhas definitivas (4xx) não repetem.
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # segundos; dobra a cada tentativa (1s, 2s)


class EmbeddingProviderError(RuntimeError):
    """Falha de chamada ao provedor de embeddings após esgotar as tentativas de retry."""


async def _post_with_retry(
    provider: str, client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> httpx.Response:
    delay = RETRY_BASE_DELAY
    status = "desconhecido"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        retriable = True
        try:
            resp = await client.post(url, json=payload)
        except httpx.TimeoutException:
            status = "timeout"
        except httpx.TransportError as exc:
            status = f"transporte:{type(exc).__name__}"
        else:
            status = str(resp.status_code)
            if resp.status_code < 400:
                return resp
            retriable = resp.status_code == 429 or resp.status_code >= 500
        if not retriable or attempt == MAX_ATTEMPTS:
            break
        await asyncio.sleep(delay)
        delay *= 2
    # Só provider + status: nunca conteúdo dos textos nem chaves.
    get_logger().warning("embedding: provider=%s falhou (status=%s) após %d tentativa(s)", provider, status, attempt)
    raise EmbeddingProviderError(f"Provedor de embeddings '{provider}' falhou (status {status})")


class EmbeddingProvider(ABC):
    batch_size = 64
    name = "base"

    @abstractmethod
    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]: ...

    async def embed(self, texts: list[str], input_type: InputType = "document") -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for i in range(0, len(texts), self.batch_size):
            vectors = await self._embed_batch(texts[i : i + self.batch_size], input_type)
            for v in vectors:
                arr = np.asarray(v, dtype=np.float32)
                if arr.shape != (EMBEDDING_DIM,):
                    raise ValueError(f"Embedding com dimensão {arr.shape}, esperado ({EMBEDDING_DIM},)")
                out.append(arr)
        return out

    async def embed_one(self, text: str, input_type: InputType = "document") -> np.ndarray:
        return (await self.embed([text], input_type))[0]

    async def warmup(self) -> None:
        """Pré-carrega recursos pesados no boot (só LocalProvider tem algo a fazer)."""
        return None

    async def aclose(self) -> None:
        """Fecha recursos alocados (clients httpx). Chamado no shutdown do lifespan."""
        return None


class VoyageProvider(EmbeddingProvider):
    name = "voyage"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise RuntimeError("VOYAGE_API_KEY não configurada")
        self.model = model or "voyage-3.5"
        self.client = httpx.AsyncClient(
            base_url="https://api.voyageai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        resp = await _post_with_retry(
            self.name,
            self.client,
            "/embeddings",
            {
                "input": texts,
                "model": self.model,
                "input_type": input_type,
                "output_dimension": EMBEDDING_DIM,
            },
        )
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    async def aclose(self) -> None:
        await self.client.aclose()


class OpenAIProvider(EmbeddingProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY não configurada")
        self.model = model or "text-embedding-3-small"
        self.client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        resp = await _post_with_retry(
            self.name,
            self.client,
            "/embeddings",
            {"input": texts, "model": self.model, "dimensions": EMBEDDING_DIM},
        )
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    async def aclose(self) -> None:
        await self.client.aclose()


class HuggingFaceProvider(EmbeddingProvider):
    """Hugging Face Inference API (pipeline feature-extraction).

    Padrão BAAI/bge-m3: 1024 dims, bom em PT-BR — o mesmo modelo do provider "local", sem baixar ~2 GB.
    Repete em 429/503 (limite de taxa / modelo "acordando" no plano gratuito).
    """

    batch_size = 32
    retries = 4
    name = "huggingface"

    def __init__(
        self,
        api_key: str,
        model: str,
        api_url: str = "",
        batch_size: int = 0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError("HF_API_KEY não configurada")
        self.model = model or "BAAI/bge-m3"
        self.url = api_url or (
            f"https://router.huggingface.co/hf-inference/models/{self.model}/pipeline/feature-extraction"
        )
        if batch_size:
            self.batch_size = batch_size
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}", "X-Wait-For-Model": "true"},
            timeout=120,
            transport=transport,
        )

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        for attempt in range(self.retries):
            resp = await self.client.post(self.url, json={"inputs": texts})
            if resp.status_code in (429, 503) and attempt < self.retries - 1:
                await asyncio.sleep(2**attempt)
                continue
            resp.raise_for_status()
            break
        vectors = resp.json()
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ValueError("Resposta inesperada do Hugging Face: esperado um vetor por texto.")
        return vectors

    async def aclose(self) -> None:
        await self.client.aclose()


class LocalProvider(EmbeddingProvider):
    """bge-m3 local (extra 'local', ~2 GB). O modelo carrega na 1ª chamada, fora do event loop."""

    batch_size = 16
    name = "local"

    def __init__(self, model: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Instale o extra 'local': uv sync --extra local") from e
        self._st_cls: Any = SentenceTransformer
        self._model_name = model or "BAAI/bge-m3"
        self.model: Any = None

    async def _load_model(self) -> None:
        if self.model is None:
            self.model = await asyncio.to_thread(self._st_cls, self._model_name)
            get_logger().info("embedding: modelo local '%s' carregado", self._model_name)

    async def warmup(self) -> None:
        await self._load_model()

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        await self._load_model()
        vectors = await asyncio.to_thread(self.model.encode, texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]


class FakeProvider(EmbeddingProvider):
    """Determinístico, sem semântica real: bag-of-words com hashing. Só para testes e dev."""

    name = "fake"

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIM
        for word in re.findall(r"\w+", text.lower()):
            h = int.from_bytes(hashlib.md5(word.encode()).digest()[:4], "little")
            v[h % EMBEDDING_DIM] += 1.0
        if not any(v):
            v[0] = 1.0
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]


@lru_cache
def get_embedder() -> EmbeddingProvider:
    s = get_settings()
    match s.embedding_provider:
        case "voyage":
            return VoyageProvider(s.voyage_api_key, s.embedding_model)
        case "openai":
            return OpenAIProvider(s.openai_api_key, s.embedding_model)
        case "huggingface":
            return HuggingFaceProvider(s.hf_api_key, s.embedding_model, s.embedding_api_url, s.embedding_batch_size)
        case "local":
            return LocalProvider(s.embedding_model)
        case "fake":
            return FakeProvider()
    raise RuntimeError(f"Provedor de embeddings desconhecido: {s.embedding_provider}")


async def close_embedder() -> None:
    """Fecha o embedder em cache (clients httpx) e libera o lru_cache. Chamado no shutdown."""
    if get_embedder.cache_info().currsize:
        await get_embedder().aclose()
        get_embedder.cache_clear()
