"""top_neighbors (plan-web-22): mesmos vizinhos que a força bruta, em blocos, sem o próprio nó."""

import numpy as np
import pytest

from mcp_rag_api.core import graph


def _brute(vectors: np.ndarray, k: int) -> list[list[int]]:
    unit = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)
    return [list(np.argsort(-row, kind="stable")[:k]) for row in sims]


@pytest.mark.parametrize(("n", "k", "block"), [(50, 5, 512), (1300, 20, 512), (37, 36, 8), (10, 3, 4)])
def test_matches_brute_force_across_blocks(monkeypatch, n, k, block):
    monkeypatch.setattr(graph, "_BLOCK_ROWS", block)
    rng = np.random.default_rng(42)
    vectors = rng.normal(size=(n, 64)).astype(np.float32)
    idx, sims = graph.top_neighbors(vectors, k)
    assert idx.shape == (n, min(k, n - 1))
    assert [list(r) for r in idx] == _brute(vectors, k)
    assert np.all(np.diff(sims, axis=1) <= 1e-7)  # ordenados do mais para o menos parecido
    assert not np.any(idx == np.arange(n)[:, None])  # nunca o próprio nó


def test_tiny_and_zero_vectors():
    assert graph.top_neighbors(np.ones((1, 4), dtype=np.float32), 5)[0].shape == (1, 0)
    vectors = np.array([[0, 0, 0], [1, 0, 0], [0.9, 0.1, 0]], dtype=np.float32)  # vetor nulo não quebra
    idx, _ = graph.top_neighbors(vectors, 1)
    assert idx[1][0] == 2 and idx[2][0] == 1
