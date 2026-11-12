"""Estimativa de tokens (caracteres ÷ 4) e corte de texto por orçamento.

É estimativa de propósito: sem tokenizer, sem dependência e igual para todo modelo. Serve para
limitar respostas (plan-web-33) e para o medidor de economia (plan-web-35), nunca para cobrança.
"""

import json
import math
from typing import Any

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def estimate_json_tokens(value: Any) -> int:
    """Tokens de um valor como ele sai serializado para o modelo (JSON compacto)."""
    return estimate_tokens(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")))


def trim_to_tokens(text: str, budget: int) -> tuple[str, bool]:
    """Corta o texto para caber no orçamento, de preferência num fim de parágrafo (ou de linha).

    Devolve (texto, cortou). O pedaço devolvido é sempre um prefixo exato do original, então quem
    continua de `len(pedaço)` remonta o texto sem perder nem repetir nada.
    """
    limit = max(budget, 1) * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text, False
    for sep in ("\n\n", "\n", " "):
        at = text.rfind(sep, 0, limit)
        if at >= limit // 2:
            return text[: at + len(sep)], True  # o separador fica no pedaço lido
    return text[:limit], True
