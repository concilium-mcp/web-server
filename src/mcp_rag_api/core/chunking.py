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

    # Unidades = pedaços de parágrafos (com o id do parágrafo para saber onde cabe "\n\n");
    # parágrafos gigantes são quebrados em pedaços de max_words.
    units: list[tuple[int, list[str]]] = []
    for para_id, para in enumerate(text.split("\n\n")):
        words = para.split(" ")
        for i in range(0, len(words), max_words):
            units.append((para_id, words[i : i + max_words]))

    # current = spans (parágrafo, palavras); current_words conta só palavras, sem separadores.
    chunks: list[list[tuple[int, list[str]]]] = []
    current: list[tuple[int, list[str]]] = []
    current_words = 0
    for para_id, unit in units:
        if current and current_words + len(unit) > max_words:
            chunks.append(current)
            # overlap: recua pelos spans do fim do chunk até juntar overlap_words palavras
            tail: list[tuple[int, list[str]]] = []
            remaining = overlap_words
            for pid, words in reversed(current):
                if remaining <= 0:
                    break
                if len(words) <= remaining:
                    tail.insert(0, (pid, words))
                    remaining -= len(words)
                else:
                    tail.insert(0, (pid, words[-remaining:]))
                    remaining = 0
            current = tail
            current_words = sum(len(w) for _, w in current)
            if current_words + len(unit) > max_words:
                current = []
                current_words = 0
        current.append((para_id, unit))
        current_words += len(unit)
    if current:
        chunks.append(current)

    out: list[Chunk] = []
    for i, spans in enumerate(chunks):
        # separador: "\n\n" entre parágrafos distintos, espaço na continuação do mesmo parágrafo
        parts: list[str] = []
        prev_pid: int | None = None
        for pid, words in spans:
            part = " ".join(words)
            if prev_pid is None:
                parts.append(part)
            elif pid == prev_pid:
                parts.append(" " + part)
            else:
                parts.append("\n\n" + part)
            prev_pid = pid
        out.append(Chunk(index=i, content="".join(parts).strip(), word_count=sum(len(w) for _, w in spans)))
    return out
