"""Grafo da base RAG: nós ligados por similaridade de cosseno real dos embeddings.

Nível "documents" usa o centroide de cada documento; nível "chunks" usa os embeddings dos chunks
(limitado a MAX_CHUNK_NODES por causa do navegador). Arestas = top-k vizinhos de cada nó, filtrados
por threshold de similaridade (kind "semantic"). No nível "documents" entram também os links
explícitos (kind "link": [[wikilinks]] e manuais), independentes do threshold; se o par também é
vizinho semântico, a aresta vira "link" com a similaridade junto.

Layout e grupos (plan-web-26): no nível "documents" cada nó vem com x,y e o grupo por assunto, do
layout calculado no servidor (core/graph_layout.py) — o navegador não roda física. Bases grandes abrem
na visão de grupos (um nó por grupo, view="groups"); group=N devolve só as notas daquele grupo.

Desempenho (plan-web-22): os vetores vêm numa consulta só e os vizinhos são calculados em memória
com numpy (produto de matrizes em blocos, numa thread). Antes era um LATERAL por nó no Postgres que
não usava o índice vetorial: com ~1.900 documentos levava ~2,3 s e, no nível chunks, ~3,4 min
prendendo o banco. O resultado (top-MAX_K vizinhos de cada nó) fica em cache por nível/coleção,
invalidado pela assinatura da base (_STAMP_SQL) — slider de similaridade e top-k só filtram.
"""

import asyncio
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from ..db import pool, records
from ..logging_config import get_logger
from ..security import Principal
from . import folders as folders_core
from . import graph_layout

MAX_CHUNK_NODES = 2000  # teto do nível "chunks" para não derrubar o navegador
GROUPS_AUTO_MIN = 1500  # acima disso a visão "auto" abre nos grupos
GROUP_EDGES_PER_GROUP = 3  # na visão de grupos, as ligações mais fortes de cada grupo
MAX_K = 20  # maior top-k aceito pelo endpoint: o cache guarda esses vizinhos e filtra por k/threshold
_BLOCK_ROWS = 512  # linhas por bloco do produto de matrizes (memória ≈ bloco × n × 4 bytes)
_CACHE_SIZE = 8
_CACHE_TTL_S = 600  # rede de segurança além da assinatura (ex.: reindex que não mexe em updated_at)

_DOC_NODES_SQL = """
    SELECT d.id, d.title, c.name AS collection, coalesce(ch.n, 0) AS chunks, d.centroid AS vector
    FROM documents d
    JOIN collections c ON c.id = d.collection_id
    LEFT JOIN (SELECT document_id, count(*) AS n FROM chunks GROUP BY document_id) ch ON ch.document_id = d.id
    WHERE d.status = 'active' AND d.centroid IS NOT NULL
      AND ($1::text IS NULL OR c.name = $1)
      AND ($2::uuid[] IS NULL OR d.folder_id = ANY($2))
    ORDER BY c.name, d.title
"""

_CHUNK_NODES_SQL = """
    SELECT c.id, c.document_id, c.chunk_index, d.title, col.name AS collection, c.embedding AS vector
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN collections col ON col.id = d.collection_id
    WHERE d.status = 'active'
      AND ($1::text IS NULL OR col.name = $1)
      AND ($3::uuid[] IS NULL OR d.folder_id = ANY($3))
    ORDER BY c.id
    LIMIT $2
"""

_LINK_EDGES_SQL = """
    SELECT l.source_id AS source, l.target_id AS target, l.kind AS link_kind, l.note,
           round((1 - (s.centroid <=> t.centroid))::numeric, 4)::float AS similarity
    FROM document_links l
    JOIN documents s ON s.id = l.source_id
    JOIN documents t ON t.id = l.target_id
    JOIN collections cs ON cs.id = s.collection_id
    JOIN collections ct ON ct.id = t.collection_id
    WHERE s.status = 'active' AND t.status = 'active'
      AND s.centroid IS NOT NULL AND t.centroid IS NOT NULL
      AND ($1::text IS NULL OR (cs.name = $1 AND ct.name = $1))
"""

# Assinatura barata (~2 mil linhas, poucos ms) de tudo o que muda o grafo: documentos ativos (conteúdo,
# arquivamento, coleção, pasta), links e chunks. Mudou a assinatura, o cache é recalculado.
_STAMP_SQL = """
    SELECT (SELECT count(*) FROM documents WHERE status = 'active'),
           (SELECT max(updated_at) FROM documents),
           (SELECT coalesce(sum(hashtext(concat_ws(':', id, collection_id, folder_id))), 0)
              FROM documents WHERE status = 'active'),
           (SELECT count(*) FROM document_links),
           (SELECT max(created_at) FROM document_links),
           (SELECT count(*) FROM chunks)
"""


def top_neighbors(vectors: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Top-k vizinhos por cosseno de cada linha (sem ela mesma), do mais para o menos parecido.

    Produto de matrizes em blocos de _BLOCK_ROWS linhas: com ~6 mil vetores a matriz inteira de
    similaridades teria ~150 MB; em blocos o pico fica em poucos MB."""
    n = len(vectors)
    k = min(k, n - 1)
    if k <= 0:
        return np.empty((n, 0), dtype=np.int64), np.empty((n, 0), dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = (vectors / norms).astype(np.float32, copy=False)
    idx = np.empty((n, k), dtype=np.int64)
    sims = np.empty((n, k), dtype=np.float32)
    for start in range(0, n, _BLOCK_ROWS):
        block = unit[start : start + _BLOCK_ROWS] @ unit.T
        rows = np.arange(block.shape[0])
        block[rows, start + rows] = -np.inf  # o próprio nó não é vizinho
        part = np.argpartition(-block, k - 1, axis=1)[:, :k]
        part_sims = np.take_along_axis(block, part, axis=1)
        order = np.argsort(-part_sims, axis=1, kind="stable")
        end = start + block.shape[0]
        idx[start:end] = np.take_along_axis(part, order, axis=1)
        sims[start:end] = np.take_along_axis(part_sims, order, axis=1)
    return idx, sims


@dataclass
class _Base:
    """Nós de um nível/coleção + top-MAX_K vizinhos de cada um (o que é caro de calcular)."""

    stamp: tuple
    built_at: float
    nodes: list[dict]
    neighbor_idx: np.ndarray
    neighbor_sim: np.ndarray
    explicit: list[dict]


_cache: "OrderedDict[tuple, _Base]" = OrderedDict()
_locks: dict[tuple, asyncio.Lock] = {}


def clear_cache() -> None:
    _cache.clear()


def _vectors_and_neighbors(rows: list) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        return np.empty((0, 0), dtype=np.int64), np.empty((0, 0), dtype=np.float32)
    matrix = np.stack([r["vector"].to_numpy().astype(np.float32, copy=False) for r in rows])
    return top_neighbors(matrix, MAX_K)


async def _base(level: str, collection: str | None, folder_ids: list | None = None) -> _Base:
    key = (level, collection, tuple(sorted(str(f) for f in folder_ids)) if folder_ids else None)
    stamp = tuple(await pool().fetchrow(_STAMP_SQL))
    cached = _cache.get(key)
    if cached and cached.stamp == stamp and time.monotonic() - cached.built_at < _CACHE_TTL_S:
        _cache.move_to_end(key)
        return cached
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:  # duas abas pedindo o mesmo grafo: calcula uma vez só
        cached = _cache.get(key)
        if cached and cached.stamp == stamp and time.monotonic() - cached.built_at < _CACHE_TTL_S:
            return cached
        if level == "chunks":
            rows = await pool().fetch(_CHUNK_NODES_SQL, collection, MAX_CHUNK_NODES, folder_ids)
            explicit: list[dict] = []
        else:
            rows = await pool().fetch(_DOC_NODES_SQL, collection, folder_ids)
            explicit = records(await pool().fetch(_LINK_EDGES_SQL, collection))
        # CPU fora do event loop: o servidor roda com 1 worker e não pode travar as outras requisições
        neighbor_idx, neighbor_sim = await asyncio.to_thread(_vectors_and_neighbors, rows)
        nodes = records(rows)
        for n in nodes:
            n.pop("vector", None)
        node_ids = {n["id"] for n in nodes}
        explicit = [e for e in explicit if e["source"] in node_ids and e["target"] in node_ids]
        base = _Base(stamp, time.monotonic(), nodes, neighbor_idx, neighbor_sim, explicit)
        _cache[key] = base
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
        return base


def _merge_link_edges(semantic: list[dict], explicit: list[dict]) -> list[dict]:
    """Une as arestas semânticas com os links explícitos: um par ligado vira kind "link"."""
    by_pair: dict[frozenset[str], dict] = {}
    for e in semantic:
        by_pair[frozenset((e["source"], e["target"]))] = {**e, "kind": "semantic"}
    for link in explicit:
        pair = frozenset((link["source"], link["target"]))
        current = by_pair.get(pair)
        if current and current["kind"] == "link":
            # A -> B e B -> A (ou wikilink + manual): uma aresta só, juntando os tipos
            current["link_kinds"] = sorted({*current["link_kinds"], link["link_kind"]})
            current["note"] = current["note"] or link["note"]
            continue
        by_pair[pair] = {
            "source": link["source"],
            "target": link["target"],
            "similarity": link["similarity"],
            "kind": "link",
            "link_kinds": [link["link_kind"]],
            "note": link["note"],
        }
    # links primeiro, depois semânticas da mais forte para a mais fraca
    return sorted(by_pair.values(), key=lambda e: (e["kind"] != "link", -(e["similarity"] or 0)))


def _dedupe_edges(rows: list[dict]) -> list[dict]:
    """Arestas dirigidas (cada nó lista seus top-k): mantém cada par uma única vez."""
    best: dict[frozenset[str], dict] = {}
    for r in rows:
        pair = frozenset((r["source"], r["target"]))
        if pair not in best or r["similarity"] > best[pair]["similarity"]:
            best[pair] = r
    return sorted(best.values(), key=lambda e: -e["similarity"])


async def warm_layout() -> None:
    """Calcula (ou carrega do Postgres) o layout e os vizinhos da base inteira ao subir o servidor, fora
    das requisições: a primeira abertura do grafo não espera o cálculo."""
    try:
        base = await _base("documents", None)  # vizinhos da base inteira (a 1ª abertura sem filtro)
        await graph_layout.get_layout(repr(base.stamp))
    except Exception:  # noqa: BLE001 — aquecimento é otimização; erro aqui não derruba o boot
        get_logger().warning("graph layout: aquecimento falhou", exc_info=True)


def _group_view(nodes: list[dict], edges: list[dict], labels: list[str]) -> tuple[list[dict], list[dict]]:
    """Um nó por grupo (tamanho, posição média e títulos mais conectados) e arestas entre grupos
    (quantas ligações há entre eles, no filtro atual)."""
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    members: dict[int, list[dict]] = {}
    for n in nodes:
        if "group" in n:
            members.setdefault(n["group"], []).append(n)
    group_nodes = []
    for g, ms in sorted(members.items()):
        top = sorted(ms, key=lambda n: -degree.get(n["id"], 0))[:3]
        collections = Counter(n["collection"] for n in ms)
        group_nodes.append(
            {
                "id": f"group:{g}",
                "group": g,
                "title": labels[g] if g < len(labels) else "—",
                "size": len(ms),
                "collection": collections.most_common(1)[0][0],
                "x": round(sum(n["x"] for n in ms) / len(ms), 1),
                "y": round(sum(n["y"] for n in ms) / len(ms), 1),
                "top": [n["title"] for n in top],
            }
        )
    group_of = {n["id"]: n["group"] for n in nodes if "group" in n}
    pairs: dict[tuple[int, int], list[float]] = {}
    for e in edges:
        a, b = group_of.get(e["source"]), group_of.get(e["target"])
        if a is None or b is None or a == b:
            continue
        pairs.setdefault((min(a, b), max(a, b)), []).append(e["similarity"] or 0)
    keep: set[tuple[int, int]] = set()
    by_group: dict[int, list[tuple[int, tuple[int, int]]]] = {}
    for pair, sims in pairs.items():
        for g in pair:
            by_group.setdefault(g, []).append((len(sims), pair))
    for ranked in by_group.values():
        keep.update(pair for _, pair in sorted(ranked, reverse=True)[:GROUP_EDGES_PER_GROUP])
    group_edges = [
        {
            "source": f"group:{a}",
            "target": f"group:{b}",
            "kind": "group",
            "weight": len(pairs[(a, b)]),
            "similarity": round(sum(pairs[(a, b)]) / len(pairs[(a, b)]), 4),
        }
        for a, b in sorted(keep)
    ]
    return group_nodes, group_edges


async def build_graph(
    level: Literal["documents", "chunks"],
    collection: str | None,
    min_similarity: float,
    k: int,
    folder_id: str | None = None,
    principal: Principal | None = None,
    view: Literal["auto", "all", "groups"] = "auto",
    group: int | None = None,
) -> dict:
    """folder_id: só as notas dessa pasta e das subpastas (plan-web-23). view/group: ver o docstring do módulo."""
    folder_ids = None
    if folder_id:
        reader = principal or Principal(actor="graph", scopes=frozenset({"read"}))
        async with pool().acquire() as conn:
            folder_ids = await folders_core.folder_subtree_ids(conn, reader, folder_id)
    base = await _base(level, collection, folder_ids)
    nodes = base.nodes
    k = max(0, min(k, base.neighbor_idx.shape[1] if base.neighbor_idx.ndim == 2 else 0))
    edges = []
    for i in range(len(nodes)):
        for j, sim in zip(base.neighbor_idx[i, :k], base.neighbor_sim[i, :k], strict=True):
            if sim < min_similarity:
                break  # vizinhos vêm ordenados: o resto é mais fraco
            edges.append({"source": nodes[i]["id"], "target": nodes[j]["id"], "similarity": round(float(sim), 4)})
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n["collection"]] = counts.get(n["collection"], 0) + 1
    payload: dict[str, Any] = {
        "level": level,
        "view": "all",
        "total_nodes": len(nodes),
        "collections": [{"name": name, "count": count} for name, count in sorted(counts.items())],
        "nodes": nodes,
        "edges": _merge_link_edges(_dedupe_edges(edges), base.explicit),
    }
    if level != "documents" or not nodes:
        return payload
    layout = await graph_layout.get_layout(repr(base.stamp))
    placed = []
    for n in nodes:
        at = layout.index.get(n["id"])
        # cópia: base.nodes fica em cache e é compartilhado entre requisições
        placed.append(
            {
                **n,
                "x": round(float(layout.xy[at, 0]), 1),
                "y": round(float(layout.xy[at, 1]), 1),
                "group": int(layout.cluster[at]),
            }
            if at is not None
            else n
        )
    payload["nodes"] = placed
    if group is not None:
        keep = {n["id"] for n in placed if n.get("group") == group}
        payload["nodes"] = [n for n in placed if n["id"] in keep]
        payload["edges"] = [e for e in payload["edges"] if e["source"] in keep and e["target"] in keep]
        payload["view"] = "group"
        payload["group"] = {
            "id": group,
            "label": layout.labels[group] if group < len(layout.labels) else "—",
            "size": len(keep),
        }
    elif view == "groups" or (view == "auto" and len(placed) > GROUPS_AUTO_MIN):
        payload["nodes"], payload["edges"] = _group_view(placed, payload["edges"], layout.labels)
        payload["view"] = "groups"
    return payload
