"""Respostas enxutas (plan-web-33): tokens, snippet, outline/section/offset e schema compacto."""

import pytest

from mcp_rag_api.config import get_settings
from mcp_rag_api.core import lean
from mcp_rag_api.core.tokens import estimate_json_tokens, estimate_tokens, trim_to_tokens
from mcp_rag_api.security import NotFound

DOC = """# Changelog

Intro do documento.

## 2026-10-07 — Auditoria

Texto da auditoria.

### Detalhe

Subseção da auditoria.

```bash
# isto é comentário de shell, não título
```

## 2026-10-10 — Pastas

Texto das pastas.

## 2026-10-07 — Outra entrada

Texto repetido.
"""


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_json_tokens({"a": "b"}) == estimate_tokens('{"a":"b"}')


def test_trim_keeps_prefix_and_cuts_on_paragraph():
    text = "primeiro paragrafo aqui.\n\nsegundo paragrafo bem maior que o orcamento permite ler."
    piece, cut = trim_to_tokens(text, 8)
    assert cut and text.startswith(piece) and piece.endswith("\n\n")
    assert trim_to_tokens("curto", 100) == ("curto", False)


def test_trim_reassembles_whole_text_by_offset():
    text = "\n\n".join(f"Paragrafo {i} " + "palavra " * 30 for i in range(40))
    out, offset = [], 0
    while True:
        piece, cut = trim_to_tokens(text[offset:], 50)
        out.append(piece)
        offset += len(piece)
        if not cut:
            break
    assert "".join(out) == text


def test_snippet_centers_on_query_term_and_marks_cuts():
    words = [f"w{i}" for i in range(300)]
    words[200] = "Orçamento"
    snippet = lean.make_snippet(" ".join(words), "orcamento anual", 30)
    assert "Orçamento" in snippet
    assert snippet.startswith("… ") and snippet.endswith(" …")
    assert len(snippet.split()) <= 32


def test_snippet_without_hit_starts_at_beginning():
    text = " ".join(f"w{i}" for i in range(100))
    snippet = lean.make_snippet(text, "nada", 10)
    assert snippet.startswith("w0 ") and snippet.endswith(" …")
    assert lean.make_snippet("texto curto", "x", 10) == "texto curto"


def test_outline_ignores_code_fences_and_nests_sections():
    heads = lean.parse_outline(DOC)
    assert [h["title"] for h in heads] == [
        "Changelog",
        "2026-10-07 — Auditoria",
        "Detalhe",
        "2026-10-10 — Pastas",
        "2026-10-07 — Outra entrada",
    ]
    audit = heads[1]
    assert "Subseção da auditoria" in DOC[audit["start"] : audit["end"]]  # inclui a subseção
    assert "Texto das pastas" not in DOC[audit["start"] : audit["end"]]


def _doc() -> dict:
    return {"id": "x", "title": "Changelog", "content": DOC}


def test_shape_outline():
    out = lean.shape_document(_doc(), outline=True)
    assert "content" not in out and out["outline"][1] == {
        "level": 2,
        "title": "2026-10-07 — Auditoria",
        "tokens": out["outline"][1]["tokens"],
    }
    assert out["tokens_estimate"] == estimate_tokens(DOC)


def test_shape_section_accent_and_case_insensitive_with_other_matches():
    out = lean.shape_document(_doc(), section="2026-10-07")
    assert out["section"] == "2026-10-07 — Auditoria"
    assert out["other_matches"] == ["2026-10-07 — Outra entrada"]
    assert "Subseção da auditoria" in out["content"] and "Texto das pastas" not in out["content"]
    assert lean.shape_document(_doc(), section="PASTAS")["section"] == "2026-10-10 — Pastas"
    with pytest.raises(NotFound):
        lean.shape_document(_doc(), section="nao existe")


def test_shape_max_tokens_and_offset():
    first = lean.shape_document(_doc(), max_tokens=10)
    assert first["truncated"] and DOC.startswith(first["content"])
    second = lean.shape_document(_doc(), offset=first["next_offset"])
    assert first["content"] + second["content"] == DOC
    assert lean.shape_document(_doc(), offset=10_000)["content"] == ""


def test_auto_outline_only_for_big_docs(monkeypatch):
    monkeypatch.setenv("DOC_FULL_MAX_TOKENS", "20")
    big = lean.shape_document(_doc(), auto_outline=True)
    assert "outline" in big and "hint" in big and "content" not in big
    full = lean.shape_document(_doc())  # REST: sem auto_outline, documento inteiro
    assert full["content"] == DOC
    monkeypatch.setenv("DOC_FULL_MAX_TOKENS", "0")
    get_settings.cache_clear()
    assert lean.shape_document(_doc(), auto_outline=True)["content"] == DOC


def test_compact_drops_nulls_and_rounds():
    assert lean.compact({"a": None, "b": [{"c": None, "d": 0.123456}], "e": 0, "f": ""}) == {
        "b": [{"d": 0.123}],
        "e": 0,
        "f": "",
    }


def test_compact_schema_keeps_property_named_title():
    schema = {
        "title": "add_documentArguments",
        "type": "object",
        "properties": {
            "title": {"title": "Title", "type": "string"},
            "tags": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}], "default": None},
        },
        "required": ["title"],
    }
    assert lean.compact_schema(schema) == {
        "type": "object",
        "properties": {"title": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["title"],
    }
