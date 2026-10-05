"""Quebra de texto em chunks por palavras, respeitando parágrafos quando possível."""

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    word_count: int


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n\n{content}".encode()).hexdigest()


def chunk_text(text: str, max_words: int = 450, overlap_words: int = 60) -> list[Chunk]:
    if overlap_words >= max_words:
        raise ValueError("overlap_words deve ser menor que max_words")
    text = normalize(text)
    if not text:
        return []

    # Unidades = parágrafos; parágrafos gigantes são quebrados em pedaços de max_words.
    units: list[list[str]] = []
    for para in text.split("\n\n"):
        words = para.split(" ")
        for i in range(0, len(words), max_words):
            units.append(words[i : i + max_words])

    chunks: list[list[str]] = []
    current: list[str] = []
    for unit in units:
        if current and len(current) + len(unit) > max_words:
            chunks.append(current)
            current = current[-overlap_words:] if overlap_words else []
            if len(current) + len(unit) > max_words:
                current = []
        if current:
            current = current + ["\n\n"] + unit
        else:
            current = list(unit)
    if current:
        chunks.append(current)

    out: list[Chunk] = []
    for i, words in enumerate(chunks):
        content = " ".join(words).replace(" \n\n ", "\n\n").strip()
        out.append(Chunk(index=i, content=content, word_count=sum(1 for w in words if w != "\n\n")))
    return out
