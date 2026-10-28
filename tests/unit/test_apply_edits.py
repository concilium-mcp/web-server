"""Edição parcial de documentos (apply_edits): trocas de trecho exato e append."""

import pytest

from mcp_rag_api.core.documents import apply_edits
from mcp_rag_api.security import KBError

CHANGELOG = "# Changelog\n\n> Uma entrada por mudança.\n\n## 2026-10-14 — Antiga\n\n- item"


def test_insert_at_top_via_header_edit():
    header = "> Uma entrada por mudança."
    out = apply_edits(CHANGELOG, [{"old_text": header, "new_text": f"{header}\n\n## 2026-10-15 — Nova\n\n- novo"}])
    assert out.index("2026-10-15") < out.index("2026-10-14")
    assert out.count("## ") == 2


def test_append_adds_paragraph_at_end():
    assert apply_edits("texto\n\n", append="  fim  ") == "texto\n\nfim"


def test_edits_apply_in_order_and_empty_new_text_deletes():
    out = apply_edits("a b c", [{"old_text": "a", "new_text": "x"}, {"old_text": "x b ", "new_text": ""}])
    assert out == "c"


def test_old_text_is_normalized_like_stored_content():
    # conteúdo gravado passa pelo normalize (CRLF -> LF, espaços colapsados)
    assert apply_edits("linha um\nlinha dois", [{"old_text": "linha  um\r\n", "new_text": "1\n"}]) == "1\nlinha dois"


@pytest.mark.parametrize(
    ("old_text", "message"),
    [("inexistente", "não encontrado"), ("item", "aparece 2 vezes"), ("  ", "vazio")],
)
def test_rejects_missing_ambiguous_or_empty_old_text(old_text, message):
    with pytest.raises(KBError, match=message):
        apply_edits("item 1\nitem 2", [{"old_text": old_text, "new_text": "x"}])
