r"""Import de vault Obsidian (.zip): parsing puro e testável, sem tocar no banco.

Cada nota `.md` do zip vira um `ParsedNote` com a collection de destino, título,
tags e conteúdo já convertido para o dialeto da plataforma:

- frontmatter YAML é removido do conteúdo (vira título/tags);
- `![[embed]]` vira `[[embed]]` (embeds de mídia/anexos são descartados — fora do escopo v1);
- comentários Obsidian `%%...%%` são removidos;
- `[[alvo#âncora|alias]]` segue a regex de core/wikilinks.py (âncora/alias ignorados na resolução).

As pastas aninhadas do vault viram um nome único de collection separado por " - ",
sanitizado para o charset de `validate_collection_name` (`^[\w][\w \-]{0,79}$`)
e truncado em 79 caracteres de forma determinística (o mesmo path sempre gera o
mesmo nome; pastas distintas podem colapsar no mesmo nome após o truncamento).
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..security import KBError
from .documents import COLLECTION_NAME_RE

# Limites da v1 (constantes de propósito; se crescerem, viram settings).
MAX_ZIP_BYTES = 100 * 1024 * 1024  # 100 MB de zip
MAX_MD_FILES = 5000  # notas .md por import
MAX_NOTE_BYTES = 10 * 1024 * 1024  # nota individual descomprimida (10 MB)
MAX_TOTAL_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # trava anti zip-bomb
ROOT_COLLECTION_NAME = "Obsidian Import"  # notas direto na raiz do vault

# Extensões de anexo/mídia: `![[foto.png]]` é descartado (anexos ficam fora da v1).
_MEDIA_EXT_RE = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|bmp|avif|pdf|mp3|wav|ogg|mp4|webm|mov|mkv|zip|"
    r"docx?|xlsx?|pptx?|canvas|excalidraw|css|js)$",
    re.IGNORECASE,
)
_EMBED_RE = re.compile(r"!\[\[([^\[\]]+)\]\]")
_COMMENT_RE = re.compile(r"%%.*?%%", re.DOTALL)
_SEGMENT_RE = re.compile(r"[^\w \-]+", re.UNICODE)
_FRONTMATTER_START_RE = re.compile(r"^---[ \t]*\n")
_FRONTMATTER_END_RE = re.compile(r"^---[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class ParsedNote:
    """Uma nota do vault pronta para o upsert_document."""

    path: str  # caminho relativo no vault (vira external_id — re-import idempotente)
    collection: str
    title: str
    content: str  # markdown convertido, sem frontmatter
    tags: list[str] = field(default_factory=list)


def _sanitize_segment(segment: str) -> str:
    """Caracteres fora do charset viram espaço; espaços colapsados; sem bordas ' '/'-'."""
    cleaned = _SEGMENT_RE.sub(" ", segment)
    return re.sub(r"\s+", " ", cleaned).strip(" -")


def collection_from_path(path: str, root_name: str = ROOT_COLLECTION_NAME) -> str:
    """Pasta da nota → nome de collection; aninhamento vira " - ".

    Notas na raiz do vault caem em `root_name`. O resultado sempre casa com
    COLLECTION_NAME_RE (mesmo charset da validação da plataforma).
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    dirs = parts[:-1]
    if not dirs:
        return root_name
    name = " - ".join(s for s in (_sanitize_segment(d) for d in dirs) if s)
    name = _sanitize_segment(name)[:79].rstrip(" -")
    if not name or not COLLECTION_NAME_RE.fullmatch(name):
        # defesa estável: nunca deixa um path estranho derrubar o import inteiro
        return root_name
    return name


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Separa o frontmatter YAML do corpo. YAML ausente/inválido → ({}, texto original)."""
    if not _FRONTMATTER_START_RE.match(text):
        return {}, text
    body_start = _FRONTMATTER_START_RE.match(text).end()  # type: ignore[union-attr]
    match = _FRONTMATTER_END_RE.search(text, body_start)
    if match is None:
        return {}, text
    raw = text[body_start : match.start()]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, text[match.end() :].lstrip("\n")


def _frontmatter_tags(value: Any) -> list[str]:
    """Obsidian aceita `tags: [a, b]`, `tags: a b` e `tags: a, b` — tudo vira list[str]."""
    if isinstance(value, str):
        items = re.split(r"[,\s]+", value)
    elif isinstance(value, list):
        items = [str(v) for v in value]
    else:
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for item in items:
        tag = item.strip().lstrip("#").strip()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def convert_content(body: str) -> str:
    """Converte o dialeto Obsidian para o da plataforma (embeds e comentários)."""
    text = _COMMENT_RE.sub("", body)
    return _EMBED_RE.sub(_convert_embed, text)


def _convert_embed(match: re.Match[str]) -> str:
    target = match.group(1).strip()
    if _MEDIA_EXT_RE.search(target):
        return ""  # anexo/mídia: fora do escopo da v1 (a referência some do texto)
    return f"[[{target}]]"


def _note_title(path: str, frontmatter: dict[str, Any]) -> str:
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    stem = path.rsplit("/", 1)[-1]
    return stem[:-3].strip() if stem.lower().endswith(".md") else stem


def parse_vault(data: bytes) -> list[ParsedNote]:
    """Lê o zip e devolve as notas ordenadas por caminho (processamento determinístico).

    Levanta KBError (vira 400 no endpoint) para zip inválido, vault sem notas ou
    limites estourados; problemas de encoding viram U+FFFD (nunca derrubam o import).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise KBError("O arquivo não é um .zip válido.") from e
    with zf:
        total_uncompressed = sum(i.file_size for i in zf.infolist())
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise KBError("Zip suspeito (descompressão muito maior que o arquivo). Recuse e tente de novo.")
        # por path: a última entrada vence (zip com nome duplicado — comportamento do unzip)
        parsed_by_path: dict[str, ParsedNote] = {}
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            path = info.filename.replace("\\", "/").lstrip("/")
            parts = [p for p in path.split("/") if p not in ("", ".")]
            # dotfiles e a pasta .obsidian ficam fora; o __MACOSX dos zips macOS
            # só carrega arquivos "._*", todos pegos pela regra de ponto
            if not parts or any(p.startswith(".") for p in parts):
                continue
            if not path.lower().endswith(".md"):
                continue  # v1: só notas; anexos ficam de fora
            if info.file_size > MAX_NOTE_BYTES:
                raise KBError(
                    f"Nota '{path}' tem mais de {MAX_NOTE_BYTES // (1024 * 1024)} MB. Divida-a antes de importar."
                )
            frontmatter, body = split_frontmatter(zf.read(info).decode("utf-8", errors="replace"))
            parsed_by_path[path] = ParsedNote(
                path=path,
                collection=collection_from_path(path),
                title=_note_title(path, frontmatter),
                content=convert_content(body),
                tags=_frontmatter_tags(frontmatter.get("tags")),
            )
    notes = sorted(parsed_by_path.values(), key=lambda n: n.path)
    if not notes:
        raise KBError(
            "Nenhuma nota .md encontrada no zip (lembre-se: a pasta .obsidian e arquivos ocultos são ignorados)."
        )
    if len(notes) > MAX_MD_FILES:
        raise KBError(f"O vault tem {len(notes)} notas, acima do limite de {MAX_MD_FILES} da v1. Divida-o em partes.")
    return notes
