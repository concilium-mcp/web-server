"""Conversão de arquivos soltos para markdown (plan-web-28) — sem banco."""

import pytest

from mcp_rag_api.core import file_import
from mcp_rag_api.core.file_import import convert_file, external_id_for, file_type_for, safe_filename
from mcp_rag_api.security import KBError


def test_pdf_pages_become_paragraphs_with_page_count(make_pdf):
    converted = convert_file("Relatório Anual.pdf", make_pdf(["Receita cresceu 20%", "Despesas estaveis"]))
    assert converted.title == "Relatório Anual"
    assert converted.file_type == "pdf"
    assert converted.content == "Receita cresceu 20%\n\nDespesas estaveis"
    assert converted.metadata == {"original_filename": "Relatório Anual.pdf", "file_type": "pdf", "pages": 2}


def test_pdf_without_text_is_rejected_as_scanned(make_pdf):
    with pytest.raises(KBError, match="escaneado"):
        convert_file("scan.pdf", make_pdf([""]))


def test_corrupted_pdf_and_docx_raise_kberror():
    with pytest.raises(KBError, match="não foi possível ler o PDF"):
        convert_file("quebrado.pdf", b"%PDF-1.4 lixo")
    with pytest.raises(KBError, match="não foi possível ler o .docx"):
        convert_file("quebrado.docx", b"nao sou um zip")


def test_docx_keeps_headings_and_bold_without_needless_escapes(make_docx, docx_p):
    body = (
        docx_p("Contrato de Serviço", style="Heading1")
        + docx_p("Cláusula 1.", bold=True)
        + docx_p("Prazo de 30 dias (corridos) - renovável!")
        + docx_p("1. começa com número")
    )
    converted = convert_file("contrato.docx", make_docx(body))
    assert converted.title == "contrato"
    assert converted.content.startswith("# Contrato de Serviço\n\n__Cláusula 1.__")
    assert "Prazo de 30 dias (corridos) - renovável!" in converted.content
    # no início da linha o escape fica: senão vira lista numerada
    assert "1\\. começa com número" in converted.content


def test_docx_images_are_dropped(make_docx):
    assert file_import._MD_IMAGE_RE.sub("", "antes ![foto](data:image/png;base64,AAA) depois") == "antes  depois"
    with pytest.raises(KBError, match="vazio"):
        convert_file("vazio.docx", make_docx(""))


def test_markdown_frontmatter_becomes_title_and_tags():
    data = "---\ntitle: Ata de Reunião\ntags: [comercial, ata]\n---\n# Pauta\n\nItem 1\n".encode()
    converted = convert_file("ata.md", data)
    assert converted.title == "Ata de Reunião"
    assert converted.tags == ["comercial", "ata"]
    assert converted.content == "# Pauta\n\nItem 1"


def test_text_file_strips_nul_and_extra_blank_lines():
    converted = convert_file("notas.txt", b"linha 1\x00   \r\n\r\n\r\n\r\nlinha 2\n")
    assert converted.content == "linha 1\n\nlinha 2"
    assert converted.file_type == "text"


def test_unsupported_extension_and_size_limit(monkeypatch):
    with pytest.raises(KBError, match="formato não suportado"):
        file_type_for("planilha.xlsx")
    assert file_type_for("LEIA.MD") == "markdown"
    monkeypatch.setattr(file_import, "MAX_FILE_BYTES", 10)
    with pytest.raises(KBError, match="passa de"):
        convert_file("grande.txt", b"x" * 11)


def test_safe_filename_drops_path_and_control_chars():
    assert safe_filename("C:\\Users\\a\\Docs\\proposta.pdf") == "proposta.pdf"
    assert safe_filename("../../etc/x\n.txt") == "x.txt"
    with pytest.raises(KBError):
        safe_filename("   ")


def test_external_id_includes_folder_path():
    assert external_id_for("a.pdf", None) == "file:a.pdf"
    assert external_id_for("a.pdf", "Clientes/Acme") == "file:Clientes/Acme/a.pdf"
