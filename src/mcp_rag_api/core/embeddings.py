"""Provedores de embeddings plugáveis. Todos devolvem vetores de EMBEDDING_DIM dimensões."""

import asyncio
import hashlib
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Literal

import httpx
import numpy as np

from ..config import EMBEDDING_DIM, get_settings

InputType = Literal["document", "query"]


class EmbeddingProvider(ABC):
    batch_size = 64

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


class VoyageProvider(EmbeddingProvider):
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
        resp = await self.client.post(
            "/embeddings",
            json={
                "input": texts,
                "model": self.model,
                "input_type": input_type,
                "output_dimension": EMBEDDING_DIM,
            },
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


class OpenAIProvider(EmbeddingProvider):
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
        resp = await self.client.post(
            "/embeddings", json={"input": texts, "model": self.model, "dimensions": EMBEDDING_DIM}
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


class HuggingFaceProvider(EmbeddingProvider):
    """Hugging Face Inference API (pipeline feature-extraction).

    Padrão BAAI/bge-m3: 1024 dims, bom em PT-BR — o mesmo modelo do provider "local", sem baixar ~2 GB.
    Repete em 429/503 (limite de taxa / modelo "acordando" no plano gratuito).
    """

    batch_size = 32
    retries = 4

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


class LocalProvider(EmbeddingProvider):
    batch_size = 16

    def __init__(self, model: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Instale o extra 'local': uv sync --extra local") from e
        self.model = SentenceTransformer(model or "BAAI/bge-m3")

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        vectors = await asyncio.to_thread(self.model.encode, texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]


class FakeProvider(EmbeddingProvider):
    """Determinístico, sem semântica real: bag-of-words com hashing. Só para testes e dev."""

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
