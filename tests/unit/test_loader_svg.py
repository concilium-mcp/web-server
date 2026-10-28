"""Loader da marca: o SVG estático do boot (dashboard.html e oauth/consent.html) é o mesmo template de
static/js/loader.js — senão o boot e o loader do JS divergem em silêncio."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "mcp_rag_api" / "static"


def _template() -> str:
    match = re.search(r"const LOADER_SVG = `(.*?)`;", (STATIC / "js" / "loader.js").read_text(encoding="utf-8"), re.S)
    assert match, "LOADER_SVG não encontrado em static/js/loader.js"
    return match.group(1)


@pytest.mark.parametrize("page", ["dashboard.html", "oauth/consent.html"])
def test_boot_loader_matches_js_template(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert _template().replace("__ID__", "cl-eyes-boot") in html


def test_loader_template_has_no_script_or_inline_handlers():
    # o SVG vai direto no HTML servido com CSP script-src 'self': nada executável aqui
    svg = _template().lower()
    assert "<script" not in svg
    assert not re.search(r"\son[a-z]+\s*=", svg)
