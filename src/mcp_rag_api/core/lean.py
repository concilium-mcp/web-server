"""Respostas enxutas para agentes (plan-web-33): busca em camadas e leitura de documento por partes.

Só o web-server tem este módulo. Ele pós-processa o que o core compartilhado devolve
(`core/search.py`, `core/documents.py`) sem mudar esses arquivos.
"""

import re
import unicodedata
import uuid
from typing import Any, Literal

from ..config import get_settings
from ..db import pool
from ..security import NotFound, Principal
from . import folders, search
from .tokens import estimate_json_tokens, estimate_tokens, trim_to_tokens

Detail = Literal["snippet", "full"]

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_WORD = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    "a o as os de da do das dos e em no na nos nas um uma para por com como que se ao aos the of and to in".split()
)


# ------------------------------------------------------------------ utilidades


def _fold(text: str) -> str:
    """Minúsculas sem acento, para comparar termos da busca com o texto."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def compact(value: Any) -> Any:
    """Tira chaves com None (recursivo) e arredonda floats a 3 casas: menos tokens, mesmo significado."""
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [compact(v) for v in value]
    if isinstance(value, float):
        return round(value, 3)
    return value


def compact_schema(node: Any) -> Any:
    """JSON Schema de entrada das tools sem o que não muda o uso: `title` gerado pelo Pydantic,
    `anyOf [X, null]` (vira só X — o parâmetro continua opcional) e `default: null`."""
    if isinstance(node, list):
        return [compact_schema(v) for v in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in ("properties", "$defs") and isinstance(value, dict):
            out[key] = {name: compact_schema(schema) for name, schema in value.items()}
        else:
            out[key] = compact_schema(value)
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        other = next(s for s in any_of if s != {"type": "null"})
        out.pop("anyOf")
        out = {**other, **out}
    if "default" in out and out["default"] is None:
        out.pop("default")
    return out


# ------------------------------------------------------------------ busca em camadas


def make_snippet(content: str, query: str, words: int) -> str:
    """Janela de `words` palavras em volta do primeiro termo da busca que aparece no trecho
    (ou o começo dele), com reticências onde cortou."""
    tokens = content.split()
    if len(tokens) <= words:
        return content.strip()
    terms = [t for t in (_fold(w) for w in _WORD.findall(query)) if len(t) >= 3 and t not in _STOPWORDS]
    hit = 0
    if terms:
        folded = [_fold(t) for t in tokens]
        hit = next((i for i, w in enumerate(folded) if any(term in w for term in terms)), 0)
    start = max(0, min(hit - words // 3, len(tokens) - words))
    end = start + words
    text = " ".join(tokens[start:end])
    return ("… " if start > 0 else "") + text + (" …" if end < len(tokens) else "")


async def _folder_paths(document_ids: list[str]) -> dict[str, str | None]:
    if not document_ids:
        return {}
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, folder_id FROM documents WHERE id = ANY($1::uuid[])",
            [uuid.UUID(d) for d in document_ids],
        )
        cache: dict[uuid.UUID, str | None] = {}
        out: dict[str, str | None] = {}
        for row in rows:
            fid = row["folder_id"]
            if fid is not None and fid not in cache:
                cache[fid] = await folders._folder_path(conn, fid)
            out[str(row["id"])] = cache.get(fid) if fid is not None else None
    return out


def _apply_budget(items: list[dict], budget: int | None, text_key: str) -> tuple[list[dict], bool]:
    """Mantém os itens (em ordem) enquanto cabem no orçamento. Se nem o primeiro cabe, ele vai
    com o texto cortado — a resposta nunca volta vazia por causa do orçamento."""
    if budget is None or not items:
        return items, False
    kept: list[dict] = []
    used = 0
    for item in items:
        cost = estimate_json_tokens(item)
        if used + cost > budget:
            if not kept:
                first = dict(item)
                room = max(budget - (cost - estimate_tokens(str(first.get(text_key) or ""))), 1)
                first[text_key], _ = trim_to_tokens(str(first.get(text_key) or ""), room)
                kept.append(first)
            return kept, True
        kept.append(item)
        used += cost
    return kept, False


async def search_lean(
    p: Principal,
    query: str,
    collections: list[str] | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    top_k: int = 5,
    detail: Detail | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Busca para agentes: `snippet` = um resultado por documento com trecho curto; `full` = os trechos
    inteiros (formato antigo). Sempre {results, truncated, tokens_estimate}."""
    settings = get_settings()
    detail = detail or settings.search_default_detail
    top_k = min(max(top_k, 1), 50)
    if detail == "full":
        rows = await search.search_knowledge(p, query, collections, tags, metadata, top_k)
        results, truncated = _apply_budget([compact(r) for r in rows], max_tokens, "content")
    else:
        # pede mais trechos que o top_k para sobrarem documentos distintos depois de agrupar
        rows = await search.search_knowledge(p, query, collections, tags, metadata, min(top_k * 3, 50))
        by_doc: dict[str, dict] = {}
        for row in rows:
            doc_id = str(row["document_id"])
            if doc_id in by_doc:
                by_doc[doc_id]["more_chunks"] += 1
                continue
            if len(by_doc) >= top_k:
                continue
            by_doc[doc_id] = {
                "document_id": doc_id,
                "title": row["title"],
                "collection": row["collection"],
                "chunk_index": row["chunk_index"],
                "snippet": make_snippet(row["content"], query, settings.search_snippet_words),
                "more_chunks": 0,
            }
        paths = await _folder_paths(list(by_doc))
        items = []
        for doc_id, item in by_doc.items():
            item["folder_path"] = paths.get(doc_id)
            if not item["more_chunks"]:
                item.pop("more_chunks")
            items.append(compact(item))
        results, truncated = _apply_budget(items, max_tokens, "snippet")
    return {"results": results, "truncated": truncated, "tokens_estimate": estimate_json_tokens(results)}


# ------------------------------------------------------------------ documento por partes


def parse_outline(markdown: str) -> list[dict]:
    """Títulos Markdown (# a ######) com a posição de cada seção. A seção vai até o próximo título
    de nível igual ou maior, então inclui as subseções. Títulos dentro de bloco de código não contam."""
    heads: list[dict] = []
    pos = 0
    in_fence = False
    for line in markdown.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if _FENCE.match(stripped):
            in_fence = not in_fence
        elif not in_fence:
            m = _HEADING.match(stripped)
            if m:
                heads.append({"level": len(m.group(1)), "title": m.group(2).strip(), "start": pos})
        pos += len(line)
    for i, head in enumerate(heads):
        end = len(markdown)
        for nxt in heads[i + 1 :]:
            if nxt["level"] <= head["level"]:
                end = nxt["start"]
                break
        head["end"] = end
        head["tokens"] = estimate_tokens(markdown[head["start"] : end])
    return heads


def _outline_view(doc: dict, content: str) -> dict:
    doc["outline"] = [{"level": h["level"], "title": h["title"], "tokens": h["tokens"]} for h in parse_outline(content)]
    doc["tokens_estimate"] = estimate_tokens(content)
    return doc


def shape_document(
    doc: dict,
    *,
    outline: bool = False,
    section: str | None = None,
    offset: int = 0,
    max_tokens: int | None = None,
    auto_outline: bool = False,
) -> dict:
    """Recorta o documento pedido. `auto_outline` (só no MCP): documento acima de
    DOC_FULL_MAX_TOKENS sem nenhum parâmetro volta como outline + dica, em vez do texto inteiro."""
    content: str = doc.pop("content", "") or ""
    if outline:
        return _outline_view(doc, content)
    if section:
        needle = _fold(section.strip())
        matches = [h for h in parse_outline(content) if needle in _fold(h["title"])]
        if not matches:
            raise NotFound(f"Seção '{section}' não encontrada. Use outline=true para ver os títulos do documento.")
        chosen = matches[0]
        piece = content[chosen["start"] : chosen["end"]]
        doc["section"] = chosen["title"]
        if len(matches) > 1:
            doc["other_matches"] = [h["title"] for h in matches[1:]]
        if max_tokens:
            piece, cut = trim_to_tokens(piece, max_tokens)
            if cut:
                doc["truncated"] = True
                doc["next_offset"] = chosen["start"] + len(piece)
        doc["content"] = piece
        return doc
    offset = max(offset, 0)
    if offset >= len(content) and offset > 0:
        doc["content"] = ""
        doc["truncated"] = False
        return doc
    rest = content[offset:]
    limit = get_settings().doc_full_max_tokens
    if auto_outline and offset == 0 and not max_tokens and limit and estimate_tokens(rest) > limit:
        _outline_view(doc, content)
        doc["hint"] = (
            f'Documento grande (~{doc["tokens_estimate"]} tokens): peça uma seção com section="<título>" '
            f"ou leia em partes com max_tokens e offset."
        )
        return doc
    if max_tokens:
        piece, cut = trim_to_tokens(rest, max_tokens)
        doc["content"] = piece
        doc["truncated"] = cut
        if cut:
            doc["next_offset"] = offset + len(piece)
        return doc
    doc["content"] = rest
    return doc
