"""Import de vault Obsidian (plan-web-13): parsing puro — frontmatter, conversões,
path→collection e iteração do zip. Tudo sem banco (só funções puras de core/obsidian_import)."""

import io
import zipfile

import pytest

from mcp_rag_api.core.documents import COLLECTION_NAME_RE
from mcp_rag_api.core.obsidian_import import (
    MAX_MD_FILES,
    ROOT_COLLECTION_NAME,
    collection_from_path,
    convert_content,
    parse_vault,
    split_frontmatter,
)


def _zip(entries: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------- frontmatter


def test_frontmatter_title_and_list_tags():
    front, body = split_frontmatter("---\ntitle: Reunião de kickoff\ntags: [ata, comercial]\n---\n# Corpo\n")
    assert front == {"title": "Reunião de kickoff", "tags": ["ata", "comercial"]}
    assert body == "# Corpo\n"


def test_frontmatter_closing_marker_must_be_on_its_own_line():
    front, body = split_frontmatter("---\ntitle: x\n--- fim inline\nconteúdo")
    assert front == {} and body == "---\ntitle: x\n--- fim inline\nconteúdo"


def test_frontmatter_invalid_yaml_is_treated_as_plain_content():
    front, body = split_frontmatter("---\ntitle: [quebrado\n  : :\n---\ntexto depois")
    assert front == {}
    assert body.startswith("---")


def test_frontmatter_non_mapping_is_ignored():
    front, _ = split_frontmatter("---\n- item1\n- item2\n---\n")
    assert front == {}


def test_split_without_frontmatter():
    front, body = split_frontmatter("# só conteúdo\n")
    assert front == {} and body == "# só conteúdo\n"


# ---------------------------------------------------------------- path → collection


def test_collection_root_and_nested():
    assert collection_from_path("nota.md") == ROOT_COLLECTION_NAME
    assert collection_from_path("comercial/propostas/nota.md") == "comercial - propostas"
    assert collection_from_path("comercial\\propostas\\nota.md") == "comercial - propostas"  # zip com backslash


def test_collection_sanitizes_charset_and_matches_platform_regex():
    name = collection_from_path("Projetos @Internos/2026!! Reuniões/nota.md")
    assert COLLECTION_NAME_RE.fullmatch(name)
    assert "@" not in name and "!" not in name and "  " not in name
    assert name == "Projetos Internos - 2026 Reuniões"


def test_collection_stable_truncation_at_79_chars():
    long_path = "/".join(["pasta" + "x" * 20] * 6) + "/nota.md"
    name = collection_from_path(long_path)
    assert len(name) <= 79 and COLLECTION_NAME_RE.fullmatch(name)
    # estável: mesmo path sempre gera o mesmo nome
    assert collection_from_path(long_path) == name


def test_collection_falls_back_when_nothing_valid_remains():
    assert collection_from_path("###/###/nota.md") == ROOT_COLLECTION_NAME


# ---------------------------------------------------------------- conversões


def test_convert_embeds_and_comments():
    content = "Antes %%comentário inline%% depois\n![[Outra nota]] e ![[foto.png]] fim\n"
    converted = convert_content(content)
    assert "[[Outra nota]]" in converted
    assert "![[" not in converted and "foto.png" not in converted
    assert "comentário inline" not in converted
    assert "Antes" in converted and "depois" in converted


def test_convert_multiline_comment():
    content = "texto\n%%\nbloco\ncomentado\n%%\nfinal"
    assert convert_content(content) == "texto\n\nfinal"


def test_convert_keeps_anchor_and_alias_embeds():
    assert convert_content("![[Nota#Seção|ver]]") == "[[Nota#Seção|ver]]"


# ---------------------------------------------------------------- iteração do zip


def test_parse_vault_skips_obsidian_dotfiles_and_non_markdown():
    data = _zip(
        {
            "nota.md": "# Raiz\n",
            ".obsidian/app.json": "{}",
            ".obsidian/plugins/x/data.json": "{}",
            ".git/config": "x",
            "pasta/.secreto.md": "não entra",
            "pasta/imagem.png": "binary",
            "pasta/sub/nota2.md": "---\ntags: alfa, beta\n---\nconteúdo",
            "__MACOSX/._nota.md": "lixo do macOS",
        }
    )
    notes = parse_vault(data)
    assert [n.path for n in notes] == ["nota.md", "pasta/sub/nota2.md"]  # ordenado por path
    assert notes[0].collection == ROOT_COLLECTION_NAME
    assert notes[1].collection == "pasta - sub"
    assert notes[1].tags == ["alfa", "beta"]
    assert notes[1].title == "nota2"  # sem frontmatter title: nome do arquivo


def test_parse_vault_frontmatter_title_beats_filename():
    data = _zip({"pasta/meu-arquivo.md": "---\ntitle: Nome bonito\n---\nconteúdo"})
    (note,) = parse_vault(data)
    assert note.title == "Nome bonito"
    assert note.content == "conteúdo"  # frontmatter stripado
    assert note.tags == []


def test_parse_vault_rejects_invalid_zip():
    with pytest.raises(Exception, match="zip"):
        parse_vault("não é um zip".encode())


def test_parse_vault_rejects_zip_without_markdown():
    with pytest.raises(Exception, match="Nenhuma nota"):
        parse_vault(_zip({"só-um.txt": "olá", ".obsidian/x": "y"}))


def test_parse_vault_dedupes_repeated_path_keeping_last_entry():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("nota.md", "conteúdo antigo")
        with pytest.warns(UserWarning, match="Duplicate name"):  # zip propositalmente duplicado
            zf.writestr("nota.md", "conteúdo novo")
        zf.writestr("outra.md", "x")
    (first, second) = parse_vault(buf.getvalue())
    assert [n.path for n in (first, second)] == ["nota.md", "outra.md"]
    assert first.content == "conteúdo novo"  # última entrada do zip vence


def test_parse_vault_rejects_too_many_notes(monkeypatch):
    from mcp_rag_api.core import obsidian_import

    monkeypatch.setattr(obsidian_import, "MAX_MD_FILES", 2)
    data = _zip({f"n{i}.md": f"conteúdo {i}" for i in range(3)})
    with pytest.raises(Exception, match="limite"):
        parse_vault(data)
    assert MAX_MD_FILES == 5000  # constante pública intacta
