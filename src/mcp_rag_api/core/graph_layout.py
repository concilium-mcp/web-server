"""Layout do grafo calculado no servidor (plan-web-26): posições x,y de cada documento e grupos por assunto.

Com milhares de notas, calcular o layout no navegador (física d3 a cada abertura, na mesma thread da
interface) travava a tela por 10–20 s. Aqui o layout é calculado uma vez, gravado no Postgres
(graph_layouts) e reaproveitado em toda abertura — o navegador só desenha.

- Posições: PCA dos centroides (ponto de partida que já separa os assuntos) + refinamento por forças
  nos vizinhos semânticos (top-k) e links explícitos, com repulsão estimada por amostragem. Só numpy.
- Grupos: k-means esférico nos centroides; rótulo = termos mais característicos dos títulos do grupo.
- Base mudou pouco (notas novas/arquivadas): as existentes não se mexem; cada nova entra na média dos
  vizinhos e no grupo mais comum entre eles. Mudou muito (ou nunca calculou): recalcula tudo.

Tudo é global (base inteira, nível documentos); filtros por coleção/pasta usam o mesmo mapa.
"""

import asyncio
import json
import math
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ..db import pool
from ..logging_config import get_logger

LAYOUT_K = 8  # vizinhos semânticos que puxam cada nó no layout
FULL_RECOMPUTE_RATIO = 0.25  # notas novas+removidas desde o último cálculo completo que pedem recalcular
_ITERATIONS = 140
_REPULSION_SAMPLES = 64
_EDGE_LENGTH = 30.0

_STOPWORDS = set(
    """
    a o e de da do das dos em no na nos nas um uma uns umas para por com sem sob sobre ao aos à às que se
    ou como mais menos seu sua seus suas este esta estes estas esse essa isso aquele aquela entre até após
    pelo pela pelos pelas qual quais quando onde ser foi são nº n° num numa dá dão providências outras
    the of and to in for on with by from at an is are as or be this that de la el los las del y en con por
    para un una lei decreto municipal municipio município dispõe institui altera redação dada art artigo
    """.split()
)


@dataclass
class Layout:
    """Posições e grupos de todos os documentos ativos (nível documentos)."""

    stamp: str
    ids: list[str]
    xy: np.ndarray  # (n, 2)
    cluster: np.ndarray  # (n,) índice do grupo
    labels: list[str]  # rótulo de cada grupo
    full_n: int  # nº de nós no último cálculo completo
    changed: int  # nós novos+removidos desde então
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {doc_id: i for i, doc_id in enumerate(self.ids)}


# ---------------------------------------------------------------- funções puras (numpy)


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32, copy=False)


def pca_2d(unit: np.ndarray, iterations: int = 25, seed: int = 0) -> np.ndarray:
    """Projeção nos 2 componentes principais por iteração de subespaço (só produtos matriz×vetores:
    montar a covariância d×d custava ~1 s com d=1024)."""
    centered = unit - unit.mean(axis=0, keepdims=True)
    basis: np.ndarray = np.random.default_rng(seed).normal(size=(unit.shape[1], 2)).astype(np.float32)
    for _ in range(iterations):
        basis, _r = np.linalg.qr(centered.T @ (centered @ basis))
    proj = centered @ basis
    std = proj.std(axis=0)
    std[std == 0] = 1.0
    return (proj / std).astype(np.float64)


def force_layout(
    init: np.ndarray, src: np.ndarray, dst: np.ndarray, weight: np.ndarray, iterations: int = _ITERATIONS, seed: int = 0
) -> np.ndarray:
    """Layout por forças: molas nas arestas (comprimento ideal _EDGE_LENGTH, força pelo peso), repulsão
    entre nós estimada com uma amostra aleatória a cada passo (O(n·amostra), não O(n²)) e uma gravidade
    leve que mantém o desenho compacto. A temperatura (passo máximo) esfria linearmente."""
    n = len(init)
    if n == 0:
        return init.copy()
    rng = np.random.default_rng(seed)
    scale = _EDGE_LENGTH * math.sqrt(n) * 0.6
    start = init * scale / max(1.0, float(np.abs(init).max())) + rng.normal(0, 1e-3 * scale, init.shape)
    # x e y em vetores separados e contíguos: somar no eixo pequeno (n×m×2) custava ~3× mais
    x = np.ascontiguousarray(start[:, 0], dtype=np.float32)
    y = np.ascontiguousarray(start[:, 1], dtype=np.float32)
    w = weight.astype(np.float32)
    samples = min(_REPULSION_SAMPLES, n)
    # repulsão de Fruchterman-Reingold (C/d) somada sobre todos os nós, estimada pela amostra × n/amostra
    repulsion = np.float32((_EDGE_LENGTH**2) * (n / samples))
    soft = np.float32((_EDGE_LENGTH * 0.1) ** 2)
    gravity = np.float32(0.06)
    t0, t_min = scale * 0.1, _EDGE_LENGTH * 0.02
    for it in range(iterations):
        dx_all = np.zeros(n, dtype=np.float32)
        dy_all = np.zeros(n, dtype=np.float32)
        if len(src):
            ex, ey = x[dst] - x[src], y[dst] - y[src]
            dist = np.sqrt(ex * ex + ey * ey) + np.float32(1e-9)
            pull = w * (dist - np.float32(_EDGE_LENGTH)) / dist * np.float32(0.5)
            dx_all += np.bincount(src, pull * ex, minlength=n) - np.bincount(dst, pull * ex, minlength=n)
            dy_all += np.bincount(src, pull * ey, minlength=n) - np.bincount(dst, pull * ey, minlength=n)
        pick = rng.choice(n, size=samples, replace=False)
        rx = x[:, None] - x[pick][None, :]
        ry = y[:, None] - y[pick][None, :]
        inv = repulsion / (rx * rx + ry * ry + soft)
        dx_all += (rx * inv).sum(axis=1) - x * gravity
        dy_all += (ry * inv).sum(axis=1) - y * gravity
        temp = t_min + (t0 - t_min) * (1 - it / iterations)
        length = np.sqrt(dx_all * dx_all + dy_all * dy_all) + np.float32(1e-9)
        step = np.minimum(length, np.float32(temp)) / length
        x += dx_all * step
        y += dy_all * step
    pos = np.stack([x, y], axis=1).astype(np.float64)
    return pos - pos.mean(axis=0)


def kmeans(unit: np.ndarray, k: int, seed: int = 0, iterations: int = 12) -> np.ndarray:
    """k-means esférico (cosseno) com início k-means++; devolve o grupo de cada linha."""
    n = len(unit)
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    centers = [unit[rng.integers(n)]]
    best = np.full(n, np.inf, dtype=np.float32)
    for _ in range(1, k):
        best = np.minimum(best, 1 - unit @ centers[-1])
        p = np.clip(best, 0, None) ** 2
        total = p.sum()
        centers.append(unit[rng.choice(n, p=p / total)] if total > 0 else unit[rng.integers(n)])
    c = np.stack(centers)
    labels = np.zeros(n, dtype=np.int64)
    for _ in range(iterations):
        new = np.argmax(unit @ c.T, axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            members = unit[labels == j]
            if len(members):
                m = members.mean(axis=0)
                c[j] = m / (np.linalg.norm(m) or 1.0)
    return labels


def _tokens(title: str) -> list[str]:
    text = unicodedata.normalize("NFC", title.lower())
    return [w for w in re.findall(r"[^\W\d_]{3,}", text) if w not in _STOPWORDS]


def cluster_labels(titles: list[str], cluster: np.ndarray, k: int, top: int = 2) -> list[str]:
    """Rótulo de cada grupo: termos mais frequentes nele e raros nos outros (c-TF-IDF sobre os títulos)."""
    per: list[Counter[str]] = [Counter() for _ in range(k)]
    for title, c in zip(titles, cluster, strict=True):
        per[int(c)].update(set(_tokens(title)))
    df: Counter[str] = Counter()
    for counts in per:
        df.update(counts.keys())
    labels = []
    for counts in per:
        size = sum(counts.values()) or 1
        scored = sorted(counts.items(), key=lambda kv: (-(kv[1] / size) * math.log(1 + k / df[kv[0]]), kv[0]))
        words = [w for w, _ in scored[:top]]
        labels.append(" · ".join(w.capitalize() for w in words) if words else "—")
    return labels


def n_clusters(n: int) -> int:
    """Quantos grupos: ~raiz de n/2, entre 6 e 60 (poucos demais não separam; muitos viram ruído)."""
    return max(1, min(60, max(6, round(math.sqrt(n / 2))), n))


def compute(vectors: np.ndarray, titles: list[str], explicit: list[tuple[int, int]], seed: int = 0) -> tuple:
    """Cálculo completo: (xy, grupo de cada nó, rótulos)."""
    from .graph import top_neighbors

    n = len(vectors)
    unit = _unit(vectors)
    nbr_idx, nbr_sim = top_neighbors(unit, LAYOUT_K)
    rows = np.repeat(np.arange(n), nbr_idx.shape[1])
    src = np.concatenate([rows, np.array([a for a, _ in explicit], dtype=np.int64)])
    dst = np.concatenate([nbr_idx.reshape(-1), np.array([b for _, b in explicit], dtype=np.int64)])
    weight = np.concatenate([np.clip(nbr_sim.reshape(-1), 0.05, 1.0), np.full(len(explicit), 1.0)])
    xy = force_layout(pca_2d(unit) if n > 2 else np.zeros((n, 2)), src, dst, weight.astype(np.float64), seed=seed)
    k = n_clusters(n)
    cluster = kmeans(unit, k, seed=seed)
    return xy, cluster, cluster_labels(titles, cluster, k)


# ---------------------------------------------------------------- cache em memória + Postgres

_layout: Layout | None = None
_lock = asyncio.Lock()

_IDS_SQL = "SELECT d.id::text AS id FROM documents d WHERE d.status = 'active' AND d.centroid IS NOT NULL ORDER BY d.id"

_ROWS_SQL = """
    SELECT d.id::text AS id, d.title, d.centroid AS vector
    FROM documents d
    WHERE d.status = 'active' AND d.centroid IS NOT NULL
    ORDER BY d.id
"""

_LINKS_SQL = """
    SELECT l.source_id::text AS source, l.target_id::text AS target
    FROM document_links l
    JOIN documents s ON s.id = l.source_id AND s.status = 'active'
    JOIN documents t ON t.id = l.target_id AND t.status = 'active'
"""


def clear_cache() -> None:
    global _layout
    _layout = None


def _to_json(layout: Layout) -> str:
    return json.dumps(
        {
            "ids": layout.ids,
            "xy": np.round(layout.xy, 1).tolist(),
            "cluster": layout.cluster.tolist(),
            "labels": layout.labels,
            "full_n": layout.full_n,
            "changed": layout.changed,
        }
    )


def _from_row(row) -> Layout:
    data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
    return Layout(
        stamp=row["stamp"],
        ids=data["ids"],
        xy=np.array(data["xy"], dtype=np.float64).reshape(-1, 2),
        cluster=np.array(data["cluster"], dtype=np.int64),
        labels=data["labels"],
        full_n=data["full_n"],
        changed=data["changed"],
    )


async def _save(layout: Layout) -> None:
    await pool().execute(
        """
        INSERT INTO graph_layouts (level, stamp, computed_at, data) VALUES ('documents', $1, now(), $2::jsonb)
        ON CONFLICT (level) DO UPDATE SET stamp = EXCLUDED.stamp, computed_at = now(), data = EXCLUDED.data
        """,
        layout.stamp,
        _to_json(layout),
    )


def _incremental(old: Layout, stamp: str, ids: list[str], nbr_idx: np.ndarray) -> Layout:
    """Mantém quem já tinha posição; cada novo vai para a média dos vizinhos posicionados (e para o grupo
    mais comum entre eles). nbr_idx: vizinhos de cada id atual, em índices da lista atual."""
    n = len(ids)
    xy = np.zeros((n, 2))
    cluster = np.zeros(n, dtype=np.int64)
    known = np.zeros(n, dtype=bool)
    for i, doc_id in enumerate(ids):
        j = old.index.get(doc_id)
        if j is not None:
            xy[i], cluster[i], known[i] = old.xy[j], old.cluster[j], True
    rng = np.random.default_rng(len(ids))
    for row in np.flatnonzero(~known):
        placed = [j for j in nbr_idx[row] if j >= 0 and known[j]] if nbr_idx.size else []
        if placed:
            xy[row] = xy[placed].mean(axis=0) + rng.normal(0, _EDGE_LENGTH * 0.3, 2)
            cluster[row] = Counter(cluster[placed].tolist()).most_common(1)[0][0]
        else:
            xy[row] = rng.normal(0, _EDGE_LENGTH * 3, 2)
            cluster[row] = 0
    added = int((~known).sum())
    removed = len(set(old.ids) - set(ids))
    return Layout(stamp, ids, xy, cluster, old.labels, old.full_n, old.changed + added + removed)


async def get_layout(stamp: str) -> Layout:
    """Layout para a assinatura atual da base: memória → Postgres → incremental → cálculo completo."""
    global _layout
    if _layout and _layout.stamp == stamp:
        return _layout
    async with _lock:
        if _layout and _layout.stamp == stamp:
            return _layout
        stored = _layout
        if stored is None:
            row = await pool().fetchrow("SELECT stamp, data FROM graph_layouts WHERE level = 'documents'")
            stored = _from_row(row) if row else None
        if stored and stored.stamp == stamp:
            _layout = stored
            return stored
        ids = [r["id"] for r in await pool().fetch(_IDS_SQL)]
        current = set(ids)
        if stored:
            churn = stored.changed + len(current - set(stored.ids)) + len(set(stored.ids) - current)
            full = churn > FULL_RECOMPUTE_RATIO * max(stored.full_n, 1)
        else:
            full = True
        started = time.monotonic()
        if full:
            # vetores (o pesado) só aqui e quando há nota nova; ordem igual à de _IDS_SQL
            rows = await pool().fetch(_ROWS_SQL)
            ids = [r["id"] for r in rows]
            links = await pool().fetch(_LINKS_SQL)
            idx = {doc_id: i for i, doc_id in enumerate(ids)}
            explicit = [
                (idx[r["source"]], idx[r["target"]]) for r in links if r["source"] in idx and r["target"] in idx
            ]
            if rows:
                vectors = np.stack([r["vector"].to_numpy().astype(np.float32, copy=False) for r in rows])
                titles = [r["title"] for r in rows]
                xy, cluster, labels = await asyncio.to_thread(compute, vectors, titles, explicit)
            else:
                xy, cluster, labels = np.empty((0, 2)), np.empty(0, dtype=np.int64), []
            layout = Layout(stamp, ids, xy, cluster, labels, full_n=len(ids), changed=0)
        else:
            assert stored is not None
            new_ids = [doc_id for doc_id in ids if doc_id not in stored.index]
            nbr_idx = np.empty((len(ids), 0), dtype=np.int64)
            if new_ids:
                rows = await pool().fetch(_ROWS_SQL)
                ids = [r["id"] for r in rows]
                row_of = {doc_id: i for i, doc_id in enumerate(ids)}
                vectors = np.stack([r["vector"].to_numpy().astype(np.float32, copy=False) for r in rows])
                new_rows = [row_of[d] for d in new_ids if d in row_of]
                nbr_idx = await asyncio.to_thread(_neighbors_of_new, vectors, new_rows)
            layout = _incremental(stored, stamp, ids, nbr_idx)
        layout.full_n = layout.full_n or len(ids)
        get_logger().info(
            "graph layout: %s (%d nós, %.1f s)",
            "completo" if full else "incremental",
            len(ids),
            time.monotonic() - started,
        )
        await _save(layout)
        _layout = layout
        return layout


def _neighbors_of_new(vectors: np.ndarray, new_rows: list[int]) -> np.ndarray:
    """Vizinhos (índices) só das linhas novas; as demais ficam vazias (-1 é filtrado pelo uso)."""
    unit = _unit(vectors)
    out = np.full((len(vectors), LAYOUT_K), -1, dtype=np.int64)
    sims = unit[new_rows] @ unit.T
    for r, row in enumerate(new_rows):
        sims[r, row] = -np.inf
        out[row] = np.argsort(-sims[r])[:LAYOUT_K]
    return out
