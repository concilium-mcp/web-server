"""Fixtures compartilhadas por unit e integration: arquivos PDF/.docx mínimos montados à mão.

Montados em bytes (sem reportlab/python-docx) para não puxar dependência só de teste; o PDF
escaneado usa Pillow e pypdf, que já são dependências do app.
"""

import io
import zipfile
from collections.abc import Callable

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter


def build_pdf(pages: list[str]) -> bytes:
    """PDF 1.4 com uma linha de texto Helvetica por página (texto em latin-1, sem parênteses)."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # /Pages, preenchido depois de saber os ids das páginas
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for i, text in enumerate(pages):
        page_id = 4 + 2 * i
        kids.append(f"{page_id} 0 R")
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def build_scanned_pdf(pages: list[str]) -> bytes:
    """PDF só de imagem (como um escaneado): cada página é uma figura com o texto desenhado, sem camada de texto."""
    font = ImageFont.load_default(size=56)
    images = []
    for text in pages:
        image = Image.new("L", (1700, 2200), 255)
        ImageDraw.Draw(image).text((150, 300), text, fill=0, font=font)
        images.append(image)
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", resolution=200, save_all=True, append_images=images[1:])
    return buf.getvalue()


def merge_pdf_files(*pdfs: bytes) -> bytes:
    writer = PdfWriter()
    for pdf in pdfs:
        writer.append(io.BytesIO(pdf))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


_DOCX_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def build_docx(paragraphs_xml: str) -> bytes:
    """.docx mínimo; `paragraphs_xml` é o miolo de <w:body> (parágrafos <w:p>)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>',
        )
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {_DOCX_NS}><w:body>{paragraphs_xml}</w:body></w:document>',
        )
    return buf.getvalue()


def docx_paragraph(text: str, style: str | None = None, bold: bool = False) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


@pytest.fixture
def make_pdf() -> Callable[[list[str]], bytes]:
    return build_pdf


@pytest.fixture
def make_scanned_pdf() -> Callable[[list[str]], bytes]:
    return build_scanned_pdf


@pytest.fixture
def merge_pdfs() -> Callable[..., bytes]:
    return merge_pdf_files


@pytest.fixture
def make_docx() -> Callable[[str], bytes]:
    return build_docx


@pytest.fixture
def docx_p() -> Callable[..., str]:
    return docx_paragraph
