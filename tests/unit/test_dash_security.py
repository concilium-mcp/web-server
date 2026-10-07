"""Pacote de segurança da dashboard (plan-web-04): validação de nome de coleção
(base do XSS armazenado) e rate limiter de login — sem banco."""

import time

import pytest

from mcp_rag_api.core.dash_auth import LoginRateLimiter
from mcp_rag_api.core.documents import validate_collection_name
from mcp_rag_api.security import KBError


def test_collection_name_accepts_unicode_space_dash_and_underscore():
    for name in ["clientes", "Produto X", "meus_docs", "ação revisão", "2026 ok", "a" * 80, "é"]:
        assert validate_collection_name(name) == name


def test_collection_name_is_stripped():
    assert validate_collection_name("  clientes  ") == "clientes"


@pytest.mark.parametrize(
    "bad",
    [
        "<script>alert(1)</script>",
        "x<img src=1 onerror=alert(1)>",
        "javascript:alert(1)",
        "-começa-com-traco",  # o 1º char precisa ser letra/número/_
        "nome/com/slash",
        "nome\tcom\ttab",
        "",
        "   ",
        "a" * 81,
    ],
)
def test_collection_name_rejects_unsafe_or_malformed(bad):
    with pytest.raises(KBError, match="Nome de coleção inválido"):
        validate_collection_name(bad)


# ---------------------------------------------------------------- rate limiter


def test_limiter_allows_attempts_until_max_failures_then_blocks():
    limiter = LoginRateLimiter(max_failures=2, base_seconds=0.05)
    key = ("fulano", "127.0.0.1")
    assert limiter.blocked_for(key) == 0.0
    limiter.register_failure(key)
    assert limiter.blocked_for(key) == 0.0  # ainda dentro do limite: pode tentar de novo
    limiter.register_failure(key)  # 2ª falha atinge o limite: bloqueia a próxima tentativa
    assert limiter.blocked_for(key) > 0.0


def test_limiter_backoff_grows_and_expires():
    limiter = LoginRateLimiter(max_failures=1, base_seconds=0.04)
    key = ("beltrana", "10.0.0.1")
    limiter.register_failure(key)  # 1ª falha já atinge o limite: bloqueio curto
    first = limiter.blocked_for(key)
    assert 0 < first <= 0.04 + 1e-6
    time.sleep(first + 0.01)
    assert limiter.blocked_for(key) == 0.0
    limiter.register_failure(key)  # 2ª falha: dobra o atraso
    assert limiter.blocked_for(key) > first


def test_limiter_reset_on_success_and_key_isolation():
    limiter = LoginRateLimiter(max_failures=1, base_seconds=60)
    key = ("ciclano", "127.0.0.1")
    limiter.register_failure(key)
    limiter.register_failure(key)
    assert limiter.blocked_for(key) > 0.0
    assert limiter.blocked_for(("outro", "127.0.0.1")) == 0.0  # outro usuário livre
    assert limiter.blocked_for(("ciclano", "10.0.0.9")) == 0.0  # outro IP livre
    limiter.reset(key)
    assert limiter.blocked_for(key) == 0.0


def test_limiter_disabled_with_zero():
    limiter = LoginRateLimiter(max_failures=0)
    key = ("desligado", "127.0.0.1")
    for _ in range(50):
        limiter.register_failure(key)
    assert limiter.blocked_for(key) == 0.0
