"""OCR das páginas de PDF sem texto (plan-web-29) — motor falso, sem banco."""

import io
import shutil

import httpx
import pypdfium2 as pdfium
import pytest
from PIL import Image

from mcp_rag_api.config import get_settings
from mcp_rag_api.core import ocr
from mcp_rag_api.core.file_import import convert_file
from mcp_rag_api.core.ocr import OcrEngine, OcrError, OpenAIVisionOcr, TesseractOcr
from mcp_rag_api.security import KBError

PNG_MAGIC = b"\x89PNG"


class FakeOcr(OcrEngine):
    name = "fake"

    def __init__(self, *texts: str, error: str | None = None) -> None:
        self.texts = list(texts)
        self.error = error
        self.calls = 0

    def read_png(self, png: bytes) -> str:
        assert png.startswith(PNG_MAGIC)
        self.calls += 1
        if self.error:
            raise OcrError(self.error)
        return self.texts.pop(0)


def test_scanned_pdf_is_read_by_ocr(make_scanned_pdf):
    engine = FakeOcr("Contrato de locação\nCláusula primeira", "Assinaturas das partes")
    converted = convert_file("contrato.pdf", make_scanned_pdf(["a", "b"]), engine, 10)
    assert converted.content == "Contrato de locação\nCláusula primeira\n\nAssinaturas das partes"
    assert converted.metadata["pages"] == 2
    assert converted.metadata["ocr_pages"] == [1, 2] and converted.metadata["ocr_engine"] == "fake"


def test_mixed_pdf_only_sends_image_pages_to_ocr(make_pdf, make_scanned_pdf, merge_pdfs):
    text_page = make_pdf(["Relatorio trimestral com texto selecionavel"])
    pdf = merge_pdfs(text_page, make_scanned_pdf(["tabela"]))
    engine = FakeOcr("Tabela de receitas por mês")
    converted = convert_file("relatorio.pdf", pdf, engine, 10)
    assert engine.calls == 1
    assert converted.content == "Relatorio trimestral com texto selecionavel\n\nTabela de receitas por mês"
    assert converted.metadata["ocr_pages"] == [2]


def test_text_pdf_skips_ocr_and_has_no_ocr_metadata(make_pdf):
    engine = FakeOcr()
    converted = convert_file("texto.pdf", make_pdf(["Este PDF ja tem bastante texto"]), engine, 10)
    assert engine.calls == 0
    assert "ocr_pages" not in converted.metadata


def test_short_text_page_keeps_pdf_text_when_ocr_reads_less(make_pdf):
    converted = convert_file("capa.pdf", make_pdf(["Obrigado"]), FakeOcr("Obr"), 10)
    assert converted.content == "Obrigado"
    assert "ocr_pages" not in converted.metadata


def test_without_engine_scanned_pdf_still_fails_as_v1(make_scanned_pdf):
    with pytest.raises(KBError, match="escaneado"):
        convert_file("scan.pdf", make_scanned_pdf(["x"]))


def test_blank_ocr_result_and_ocr_failure_become_kberror(make_scanned_pdf):
    with pytest.raises(KBError, match="nem o OCR"):
        convert_file("branco.pdf", make_scanned_pdf([""]), FakeOcr(""), 10)
    with pytest.raises(KBError, match="página 1 .*tesseract caiu"):
        convert_file("erro.pdf", make_scanned_pdf(["x"]), FakeOcr(error="tesseract caiu"), 10)


def test_too_many_pages_for_ocr_is_rejected_before_running(make_scanned_pdf):
    engine = FakeOcr()
    with pytest.raises(KBError, match="3 páginas sem texto precisam de OCR e o limite deste envio é 2"):
        convert_file("grande.pdf", make_scanned_pdf(["a", "b", "c"]), engine, 2)
    assert engine.calls == 0


def test_render_page_png_respects_pixel_cap(make_scanned_pdf, monkeypatch):
    monkeypatch.setattr(ocr, "MAX_RENDER_PIXELS", 100_000)
    pdf = pdfium.PdfDocument(make_scanned_pdf(["x"]))
    png = ocr.render_page_png(pdf, 0)
    pdf.close()
    with Image.open(io.BytesIO(png)) as image:
        assert image.width * image.height <= 101_000  # arredondamento do pdfium em cada lado


def test_tesseract_nonzero_exit_raises_ocr_error():
    with pytest.raises(OcrError, match="tesseract falhou"):
        TesseractOcr("false", "por").read_png(PNG_MAGIC)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract não instalado")
def test_real_tesseract_reads_scanned_page(make_scanned_pdf):
    engine = TesseractOcr(shutil.which("tesseract") or "", "por+eng")
    converted = convert_file("scan.pdf", make_scanned_pdf(["Contrato de locacao 2026"]), engine, 10)
    assert "Contrato de locacao 2026" in converted.content


def test_openai_vision_sends_image_and_reads_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "Texto da página"}}]})

    engine = OpenAIVisionOcr("sk-x", "gpt-4o-mini", "https://llm.local/v1/", httpx.MockTransport(handler))
    assert engine.read_png(PNG_MAGIC) == "Texto da página"
    assert seen["url"] == "https://llm.local/v1/chat/completions" and seen["auth"] == "Bearer sk-x"
    assert "data:image/png;base64," in seen["body"]

    failing = OpenAIVisionOcr("k", "m", "https://llm.local", httpx.MockTransport(lambda _: httpx.Response(429)))
    with pytest.raises(OcrError, match="HTTP 429"):
        failing.read_png(PNG_MAGIC)


@pytest.fixture
def settings_env(monkeypatch):
    def apply(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        ocr.get_engine.cache_clear()

    yield apply
    get_settings.cache_clear()
    ocr.get_engine.cache_clear()


def test_get_engine_follows_ocr_provider(settings_env, monkeypatch):
    settings_env(OCR_PROVIDER="none")
    assert ocr.get_engine() is None

    settings_env(OCR_PROVIDER="tesseract")
    monkeypatch.setattr(ocr.shutil, "which", lambda _: None)
    ocr.get_engine.cache_clear()
    assert ocr.get_engine() is None  # binário ausente: desliga em vez de quebrar o import

    settings_env(OCR_PROVIDER="openai", OCR_API_KEY="sk-ocr", OCR_MODEL="modelo-x")
    engine = ocr.get_engine()
    assert isinstance(engine, OpenAIVisionOcr) and engine.model == "modelo-x"
