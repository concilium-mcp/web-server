"""dashboard.css e fine.css bem formados: uma chave perdida (ex.: resolução de conflito de merge) faz o navegador
engolir silenciosamente todos os blocos seguintes — nenhum outro teste pegaria isso."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "mcp_rag_api" / "static"
# fine.css entrou depois e ficou fora do teste: um bloco sem fechar engoliu a seção "Gasto real" inteira
SHEETS = [STATIC / "dashboard.css", STATIC / "fine.css"]
sheets = pytest.mark.parametrize("css", SHEETS, ids=lambda p: p.name)


@sheets
def test_braces_are_balanced_and_never_negative(css):
    text = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
    depth = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        depth += line.count("{") - line.count("}")
        assert depth >= 0, f"'}}' sem bloco aberto na linha {line_no} (sem contar comentários)"
    assert depth == 0, f"{depth} bloco(s) sem fechar no fim do arquivo"


@sheets
def test_no_merge_conflict_markers(css):
    text = css.read_text(encoding="utf-8")
    assert not re.search(r"^(<{7}|={7}|>{7})( |$)", text, re.M)
