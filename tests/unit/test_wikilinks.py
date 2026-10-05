from mcp_rag_api.core.wikilinks import parse_wikilinks, title_key


def test_parse_basic_alias_section_and_order():
    text = "Veja [[Autenticação]], o [[Registro de agentes|registro]] e [[Visão geral#Arquitetura]]."
    assert parse_wikilinks(text) == ["Autenticação", "Registro de agentes", "Visão geral"]


def test_parse_dedupes_ignoring_case_and_accents():
    assert parse_wikilinks("[[Autenticação]] e de novo [[autenticacao]] e [[ AUTENTICAÇÃO ]]") == ["Autenticação"]


def test_parse_ignores_empty_and_broken():
    assert parse_wikilinks("[[]] [[ ]] [[sem fechar e [simples] e [[a\nb]]") == []
    assert parse_wikilinks("") == [] and parse_wikilinks(None) == []


def test_parse_collapses_inner_spaces():
    assert parse_wikilinks("[[Plano   comercial]]") == ["Plano comercial"]


def test_title_key_matches_database_rule():
    assert title_key("  Autenticação  de   API ") == "autenticacao de api"
