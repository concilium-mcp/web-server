"""Cache de embeddings de consulta (plan-web-24): consulta repetida não chama o provedor de novo."""

import numpy as np

from mcp_rag_api.config import EMBEDDING_DIM
from mcp_rag_api.core.embeddings import EmbeddingProvider


class CountingProvider(EmbeddingProvider):
    name = "contador"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def _embed_batch(self, texts, input_type):
        self.calls.append((list(texts), input_type))
        return [[float(len(t))] * EMBEDDING_DIM for t in texts]


async def test_repeated_query_hits_cache():
    p = CountingProvider()
    a = await p.embed_one("agenda de consultas", "query")
    b = await p.embed_one("  agenda de consultas ", "query")  # espaços nas pontas: mesma consulta
    assert len(p.calls) == 1 and np.array_equal(a, b)
    assert not a.flags.writeable  # vetor compartilhado é somente leitura


async def test_documents_are_never_cached():
    p = CountingProvider()
    await p.embed_one("texto do documento", "document")
    await p.embed_one("texto do documento", "document")
    assert len(p.calls) == 2


async def test_cache_is_bounded_lru(monkeypatch):
    p = CountingProvider()
    monkeypatch.setattr(CountingProvider, "QUERY_CACHE_SIZE", 2)
    for q in ["a", "b", "a", "c"]:  # "b" é o menos recente quando "c" entra
        await p.embed_one(q, "query")
    await p.embed_one("a", "query")
    assert [c[0][0] for c in p.calls] == ["a", "b", "c"]
    await p.embed_one("b", "query")  # foi despejado: chama de novo
    assert len(p.calls) == 4
