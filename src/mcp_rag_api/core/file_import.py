"""Import de arquivos soltos (PDF, Word .docx, Markdown, texto): conversão pura e testável, sem banco.

Cada arquivo vira um `ConvertedFile` com título (nome do arquivo sem extensão), conteúdo em
markdown e metadata de origem; a gravação passa por `import_file`, que usa o upsert com
pasta do core — então chunking, embeddings, dedupe e wikilinks são os de sempre.

- PDF: texto por página com `pypdf` (sem OCR: PDF escaneado não tem texto e vira erro claro);
- .docx: `mammoth` para markdown, preservando títulos, listas, negrito e links; imagens descartadas;
- .md: frontmatter YAML vira título/tags (mesmo parser do import do Obsidian);
- .txt: texto como está.

`external_id` = "file:" + caminho da pasta + nome do arquivo: re-enviar o mesmo arquivo para o
mesmo lugar atualiza a nota (nova versão) em vez de duplicar.
"""

import io
import re
from dataclasses import dataclass, field
from typing import Any

import mammoth
from pypdf import PdfReader

from ..security import KBError, Principal
from . import folders
from .obsidian_import import _frontmatter_tags, split_frontmatter

# Limites da v1 (constantes de propósito; se crescerem, viram settings).
MAX_FILE_BYTES = 25 * 1024 * 1024  # arquivo individual
MAX_FILES = 50  # arquivos por import na dash
MAX_TOTAL_BYTES = 100 * 1024 * 1024  # soma do upload na dash
MAX_PDF_PAGES = 2000

FILE_TYPES = {".pdf": "pdf", ".docx": "docx", ".md": "markdown", ".markdown": "markdown", ".txt": "text"}
SOURCE = "file-import"

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
# o mammoth escapa toda pontuação ("30 dias\."); só o início de linha precisa disso (lista/título)
_LINE_START_MARK_RE = re.compile(r"^\s*(?:\d+\\\.|\\[-+]|(?:\\#)+)")
_HARMLESS_ESCAPE_RE = re.compile(r"\\([.!(){}+\-])")


@dataclass(frozen=True)
class ConvertedFile:
    """Um arquivo convertido, pronto para o upsert."""

    filename: str
    file_type: str  # pdf | docx | markdown | text
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def file_type_for(filename: str) -> str:
    """Tipo pelo sufixo do nome; extensão não suportada → KBError (vira 400)."""
    name = filename.lower()
    for ext, kind in FILE_TYPES.items():
        if name.endswith(ext):
            return kind
    raise KBError(f"'{filename}': formato não suportado. Envie PDF, Word (.docx), Markdown (.md) ou texto (.txt).")


def safe_filename(filename: str) -> str:
    """Só o nome final (o navegador pode mandar caminho), sem caracteres de controle."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if not name or name in (".", ".."):
        raise KBError("Arquivo sem nome.")
    return name[:200]


def _stem(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].strip() if "." in filename else filename.strip()
    return stem or filename


def _clean_text(text: str) -> str:
    """Postgres não aceita NUL em text; normaliza quebras e espaços sobrando."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE_RE.sub("\n", text)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def _convert_pdf(data: bytes, filename: str) -> tuple[str, dict[str, Any]]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise KBError(f"'{filename}': PDF protegido por senha. Remova a senha e envie de novo.")
        pages = reader.pages
        if len(pages) > MAX_PDF_PAGES:
            raise KBError(f"'{filename}': mais de {MAX_PDF_PAGES} páginas. Divida o PDF antes de importar.")
        texts = [_clean_text(page.extract_text() or "") for page in pages]
    except KBError:
        raise
    except Exception as e:  # pypdf levanta tipos variados para PDF corrompido
        raise KBError(f"'{filename}': não foi possível ler o PDF ({e}).") from e
    content = "\n\n".join(t for t in texts if t)
    if not content:
        raise KBError(
            f"'{filename}': o PDF não tem texto extraível (provavelmente escaneado; OCR ainda não é suportado)."
        )
    return content, {"pages": len(pages)}


def _unescape_markdown(text: str) -> str:
    """Tira os escapes desnecessários do mammoth, mantendo os de início de linha e os de []*_`#."""
    lines = []
    for line in text.split("\n"):
        mark = _LINE_START_MARK_RE.match(line)
        keep = mark.end() if mark else 0
        lines.append(line[:keep] + _HARMLESS_ESCAPE_RE.sub(r"\1", line[keep:]))
    return "\n".join(lines)


def _convert_docx(data: bytes, filename: str) -> str:
    try:
        # imagens viram nada (data URI em base64 incharia a nota e os chunks)
        result = mammoth.convert_to_markdown(io.BytesIO(data), convert_image=mammoth.images.img_element(lambda _: {}))
    except Exception as e:  # mammoth/zipfile/lxml levantam tipos variados para arquivo corrompido
        raise KBError(f"'{filename}': não foi possível ler o .docx ({e}).") from e
    content = _clean_text(_unescape_markdown(_MD_IMAGE_RE.sub("", result.value)))
    if not content:
        raise KBError(f"'{filename}': o documento está vazio.")
    return content


def convert_file(filename: str, data: bytes) -> ConvertedFile:
    """Converte um arquivo para markdown. Erros de conteúdo levantam KBError com o nome do arquivo."""
    filename = safe_filename(filename)
    kind = file_type_for(filename)
    if len(data) > MAX_FILE_BYTES:
        raise KBError(f"'{filename}': passa de {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    title = _stem(filename)
    tags: list[str] = []
    metadata: dict[str, Any] = {"original_filename": filename, "file_type": kind}
    if kind == "pdf":
        content, extra = _convert_pdf(data, filename)
        metadata.update(extra)
    elif kind == "docx":
        content = _convert_docx(data, filename)
    else:
        frontmatter, body = split_frontmatter(data.decode("utf-8", errors="replace"))
        content = _clean_text(body)
        if kind == "markdown":
            fm_title = frontmatter.get("title")
            if isinstance(fm_title, str) and fm_title.strip():
                title = fm_title.strip()
            tags = _frontmatter_tags(frontmatter.get("tags"))
        if not content:
            raise KBError(f"'{filename}': o arquivo está vazio.")
    return ConvertedFile(filename, kind, title, content, tags, metadata)


def external_id_for(filename: str, folder_path: str | None) -> str:
    return "file:" + "/".join(p for p in (folder_path or "", filename) if p)


async def import_file(p: Principal, collection: str, folder_path: str | None, converted: ConvertedFile) -> dict:
    """Grava (ou atualiza) a nota do arquivo na coleção/pasta — chunk + embed pelo upsert do core."""
    result = await folders.upsert_document_in_folder(
        p,
        folder_path,
        collection=collection,
        external_id=external_id_for(converted.filename, folder_path),
        title=converted.title,
        content=converted.content,
        tags=converted.tags or None,
        metadata=converted.metadata,
        source=SOURCE,
        change_note=f"importação de arquivo ({converted.filename})",
    )
    return {**result, "filename": converted.filename, "file_type": converted.file_type}
