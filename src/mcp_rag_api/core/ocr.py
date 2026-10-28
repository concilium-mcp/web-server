"""OCR das páginas de PDF que não têm texto (plan-web-29): escaneados e páginas-imagem.

A página vira PNG com `pypdfium2` e passa por um motor de OCR plugável:

- `tesseract` (padrão): binário `tesseract` no PATH, roda local — o documento não sai do servidor;
- `openai`: modelo de visão numa API compatível com OpenAI (`/chat/completions`), opcional;
- `none`: sem OCR (comportamento da v1).

Tudo aqui é síncrono: o import já roda a conversão em `asyncio.to_thread`.
"""

import base64
import io
import shutil
import subprocess
from abc import ABC, abstractmethod
from functools import lru_cache

import httpx
import pypdfium2 as pdfium

from ..config import get_settings
from ..logging_config import get_logger

RENDER_DPI = 200
# teto de pixels por página renderizada (pôster/planta A0 a 200 dpi passaria de 100 MP)
MAX_RENDER_PIXELS = 25_000_000
TESSERACT_TIMEOUT = 120  # segundos por página
OPENAI_TIMEOUT = 120.0
OPENAI_PROMPT = (
    "Transcreva todo o texto desta página exatamente como aparece, na ordem de leitura. "
    "Use markdown simples para títulos, listas e tabelas. Não comente, não resuma e não traduza. "
    "Se a página não tiver texto, responda com uma string vazia."
)


class OcrError(RuntimeError):
    """Falha do motor de OCR numa página (vira erro daquele arquivo no import)."""


class OcrEngine(ABC):
    name: str

    @abstractmethod
    def read_png(self, png: bytes) -> str:
        """Texto da imagem (PNG) de uma página."""


class TesseractOcr(OcrEngine):
    name = "tesseract"

    def __init__(self, binary: str, languages: str) -> None:
        self.binary = binary
        self.languages = languages

    def read_png(self, png: bytes) -> str:
        try:
            proc = subprocess.run(
                [self.binary, "stdin", "stdout", "-l", self.languages],
                input=png,
                capture_output=True,
                timeout=TESSERACT_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise OcrError(f"o OCR passou de {TESSERACT_TIMEOUT}s numa página") from e
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise OcrError(f"tesseract falhou ({detail[-1] if detail else proc.returncode})")
        return proc.stdout.decode("utf-8", errors="replace")


class OpenAIVisionOcr(OcrEngine):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.model = model
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=OPENAI_TIMEOUT,
            transport=transport,
        )

    def read_png(self, png: bytes) -> str:
        image_url = "data:image/png;base64," + base64.b64encode(png).decode()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OPENAI_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        }
        try:
            resp = self.client.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise OcrError(f"falha ao chamar o OCR ({type(e).__name__})") from e
        if resp.status_code >= 400:
            raise OcrError(f"o provedor de OCR respondeu HTTP {resp.status_code}")
        try:
            return str(resp.json()["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise OcrError("resposta inesperada do provedor de OCR") from e


@lru_cache
def get_engine() -> OcrEngine | None:
    """Motor configurado em OCR_PROVIDER; None = OCR desligado ou indisponível."""
    s = get_settings()
    if s.ocr_provider == "none":
        return None
    if s.ocr_provider == "openai":
        return OpenAIVisionOcr(s.ocr_api_key or s.openai_api_key, s.ocr_model, s.ocr_api_url)
    binary = shutil.which("tesseract")
    if not binary:
        get_logger().warning("OCR_PROVIDER=tesseract, mas o binário 'tesseract' não está no PATH: OCR desligado")
        return None
    return TesseractOcr(binary, s.ocr_languages)


def render_page_png(pdf: pdfium.PdfDocument, index: int, dpi: int = RENDER_DPI) -> bytes:
    """Página `index` (0-based) como PNG em tons de cinza, reduzindo a escala se passar do teto."""
    page = pdf[index]
    try:
        width, height = page.get_size()  # em pontos (1/72")
        scale = dpi / 72
        pixels = width * scale * height * scale
        if pixels > MAX_RENDER_PIXELS:
            scale *= (MAX_RENDER_PIXELS / pixels) ** 0.5
        image = page.render(scale=scale, grayscale=True).to_pil()
    finally:
        page.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
