"""Temas da dashboard (plan-web-21): contraste WCAG de cada paleta e catálogo em sincronia
(static/js/themes.js ↔ boot.js ↔ dashboard.css)."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "mcp_rag_api" / "static"
CSS = (STATIC / "dashboard.css").read_text(encoding="utf-8")
THEMES_JS = (STATIC / "js" / "themes.js").read_text(encoding="utf-8")
BOOT_JS = (STATIC / "boot.js").read_text(encoding="utf-8")

# tema original (sem data-palette): tokens do :root e do :root[data-theme="light"]
BASE_BLOCKS = {"concilium-dark": r":root \{", "concilium-light": r':root\[data-theme="light"\] \{'}


def _tokens(block_re: str) -> dict[str, str]:
    match = re.search(block_re + r"(.*?)\n\}", CSS, re.S)
    assert match, f"bloco não encontrado: {block_re}"
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1)))


def _base(mode: str) -> dict[str, str]:
    # cascata do navegador: :root por baixo; no claro, o bloco [data-theme="light"] por cima
    root = _tokens(BASE_BLOCKS["concilium-dark"])
    return {**root, **_tokens(BASE_BLOCKS["concilium-light"])} if mode == "light" else root


def _palette(theme_id: str) -> dict[str, str]:
    mode = "light" if theme_id in _ids_by_mode()["light"] else "dark"
    if theme_id in BASE_BLOCKS:
        return _base(mode)
    # tokens que a paleta não redefine vêm do modo base (ex.: edges)
    return {**_base(mode), **_tokens(r':root\[data-palette="' + re.escape(theme_id) + r'"\] \{')}


def _ids_by_mode() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"dark": [], "light": []}
    for theme_id, mode in re.findall(r'id: "([\w-]+)"[^}]*?mode: "(dark|light)"', THEMES_JS):
        out[mode].append(theme_id)
    return out


def _luminance(hex_color: str) -> float:
    h = hex_color.strip().lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


ALL_IDS = [*_ids_by_mode()["dark"], *_ids_by_mode()["light"]]

# (frente, fundo, mínimo): 4,5:1 texto normal (WCAG AA); 3:1 componentes de interface
PAIRS = [
    ("text", "bg", 4.5),
    ("text", "bg-elevated", 4.5),
    ("text-body", "bg", 4.5),
    ("text-dim", "bg", 4.5),
    ("text-dim", "bg-elevated", 4.5),
    ("text-dim", "bg-sidebar", 4.5),
    ("on-accent", "accent", 4.5),
    ("accent", "bg", 3.0),
]


@pytest.mark.parametrize("theme_id", ALL_IDS)
@pytest.mark.parametrize(("fg", "bg", "minimum"), PAIRS)
def test_palette_contrast(theme_id, fg, bg, minimum):
    # exceção conhecida, anterior aos temas: texto branco sobre o coral da marca nos temas Concilium
    # (3,1:1). Decisão de marca, não deste teste — vale o mínimo de 3:1 (texto de botão em peso médio).
    if theme_id.startswith("concilium-") and (fg, bg) == ("on-accent", "accent"):
        minimum = 3.0
    tokens = _palette(theme_id)
    ratio = _contrast(tokens[fg], tokens[bg])
    pair = f"--{fg} {tokens[fg]} sobre --{bg} {tokens[bg]}"
    assert ratio >= minimum, f"{theme_id}: {pair} = {ratio:.2f}:1 (mínimo {minimum})"


def test_catalog_has_both_modes_and_unique_ids():
    modes = _ids_by_mode()
    assert "concilium-dark" in modes["dark"] and "concilium-light" in modes["light"]
    assert len(ALL_IDS) == len(set(ALL_IDS))


def test_boot_lists_match_catalog():
    # o boot aplica o tema antes do 1º paint sem importar módulos: mesma lista de ids por modo
    for mode, var in (("dark", "DARK"), ("light", "LIGHT")):
        listed = re.search(var + r" = \[(.*?)\];", BOOT_JS).group(1)
        assert re.findall(r'"([\w-]+)"', listed) == _ids_by_mode()[mode]


@pytest.mark.parametrize("theme_id", [i for i in ALL_IDS if not i.startswith("concilium-")])
def test_every_palette_has_css_and_chart_tokens(theme_id):
    assert f':root[data-palette="{theme_id}"] {{' in CSS
    assert f':root[data-palette="{theme_id}"] .overview-page {{' in CSS
