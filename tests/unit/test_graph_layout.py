"""Layout do grafo no servidor (plan-web-26): funções puras em numpy."""

import numpy as np

from mcp_rag_api.core import graph_layout as gl
from mcp_rag_api.core.graph import top_neighbors


def _clustered(n=600, d=64, k=6, seed=1):
    rng = np.random.default_rng(seed)
    centers = rng.normal(0, 1, (k, d))
    truth = rng.integers(0, k, n)
    vectors = (centers[truth] * 0.8 + rng.normal(0, 1, (n, d))).astype(np.float32)
    return vectors, truth


def test_compute_places_neighbors_close_and_groups_by_topic():
    vectors, truth = _clustered()
    topics = [
        "saude conselho",
        "orcamento credito",
        "alvara obra",
        "transito onibus",
        "escola merenda",
        "turismo festa",
    ]
    titles = [f"Lei {i} {topics[t]}" for i, t in enumerate(truth)]
    xy, cluster, labels = gl.compute(vectors, titles, explicit=[(0, 1)])

    assert xy.shape == (len(vectors), 2) and np.isfinite(xy).all()
    # vizinhos semânticos ficam bem mais perto entre si que pares quaisquer
    idx, _ = top_neighbors(gl._unit(vectors), 5)
    near = np.linalg.norm(xy[np.repeat(np.arange(len(xy)), 5)] - xy[idx.reshape(-1)], axis=1).mean()
    rng = np.random.default_rng(0)
    pairs = rng.integers(0, len(xy), (5000, 2))
    anywhere = np.linalg.norm(xy[pairs[:, 0]] - xy[pairs[:, 1]], axis=1).mean()
    assert near < anywhere * 0.3
    # cada grupo é (quase) um tópico só
    purity = sum(np.bincount(truth[cluster == c]).max() for c in np.unique(cluster)) / len(truth)
    assert purity > 0.9
    assert len(labels) == gl.n_clusters(len(vectors))


def test_compute_is_deterministic():
    vectors, truth = _clustered(n=200)
    titles = [f"doc {t}" for t in truth]
    a = gl.compute(vectors, titles, [])
    b = gl.compute(vectors, titles, [])
    assert np.allclose(a[0], b[0]) and (a[1] == b[1]).all()


def test_cluster_labels_pick_distinctive_words():
    titles = ["Lei do conselho de saúde", "Conselho municipal de saúde", "Crédito no orçamento", "Orçamento anual"]
    labels = gl.cluster_labels(titles, np.array([0, 0, 1, 1]), 2)
    assert "Saúde" in labels[0] and "Conselho" in labels[0]
    assert "Orçamento" in labels[1]
    # palavras genéricas ("lei", "municipal") e números não viram rótulo
    assert "Lei" not in labels[0] and "Municipal" not in labels[0]


def test_n_clusters_bounds():
    assert gl.n_clusters(1) == 1
    assert gl.n_clusters(20) == 6
    assert gl.n_clusters(5697) == 53
    assert gl.n_clusters(1_000_000) == 60


def test_incremental_keeps_known_positions_and_places_new_near_neighbors():
    old = gl.Layout(
        "s1",
        ["a", "b", "c"],
        np.array([[0.0, 0.0], [100.0, 0.0], [0.0, 100.0]]),
        np.array([0, 1, 2]),
        ["x", "y", "z"],
        3,
        0,
    )
    ids = ["a", "b", "new"]  # "c" saiu, "new" entrou
    nbr = np.full((3, gl.LAYOUT_K), -1)
    nbr[2, :2] = [1, 1]  # vizinho de "new" é "b"
    layout = gl._incremental(old, "s2", ids, nbr)
    assert layout.stamp == "s2" and layout.ids == ids
    assert np.allclose(layout.xy[0], [0, 0]) and np.allclose(layout.xy[1], [100, 0])
    assert np.linalg.norm(layout.xy[2] - [100, 0]) < gl._EDGE_LENGTH * 2
    assert layout.cluster[2] == 1  # grupo do vizinho
    assert layout.changed == 2  # 1 novo + 1 removido
    assert layout.index["new"] == 2
