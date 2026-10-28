"""TOTP (RFC 6238) e recovery codes do 2FA da dashboard — funções puras, sem banco."""

import base64

import pytest

from mcp_rag_api.config import check_config, get_settings
from mcp_rag_api.core import mfa
from mcp_rag_api.security import KBError

# segredo do apêndice B da RFC 6238 (SHA-1): "12345678901234567890" em ASCII
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_totp_matches_rfc6238_vectors():
    # a RFC dá 8 dígitos; os apps usam os 6 finais
    for unix_time, expected in [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924")]:
        assert mfa.totp_at(RFC_SECRET, unix_time // 30) == expected[-6:]


def test_match_step_accepts_one_step_of_drift_only():
    now = 1_700_000_000.0
    step = mfa.current_step(now)
    for delta in (-1, 0, 1):
        assert mfa.match_step(RFC_SECRET, mfa.totp_at(RFC_SECRET, step + delta), now) == step + delta
    assert mfa.match_step(RFC_SECRET, mfa.totp_at(RFC_SECRET, step + 2), now) is None
    assert mfa.match_step(RFC_SECRET, mfa.totp_at(RFC_SECRET, step - 2), now) is None


def test_match_step_tolerates_spaces_and_rejects_garbage():
    now = 1_700_000_000.0
    code = mfa.totp_at(RFC_SECRET, mfa.current_step(now))
    assert mfa.match_step(RFC_SECRET, f"{code[:3]} {code[3:]}", now) is not None
    for bad in ["", "12345", "1234567", "abcdef"]:
        assert mfa.match_step(RFC_SECRET, bad, now) is None


def test_new_secret_is_base32_160_bits():
    secret = mfa.new_secret()
    assert len(mfa._b32decode(secret)) == 20
    assert "=" not in secret


def test_otpauth_uri_has_issuer_and_secret():
    uri = mfa.otpauth_uri("ABCDEFGH", "maria")
    assert uri.startswith("otpauth://totp/Concilium%3Amaria?")
    assert "secret=ABCDEFGH" in uri and "issuer=Concilium" in uri


def test_qr_svg_is_inline_svg():
    svg = mfa.qr_svg(mfa.otpauth_uri(mfa.new_secret(), "maria"))
    assert svg.lstrip().startswith("<svg") and "</svg>" in svg


def test_recovery_codes_are_unique_and_hashed_normalized():
    codes, hashes = mfa.new_recovery_codes()
    assert len(set(codes)) == mfa.RECOVERY_CODES == len(hashes)
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    # digitado em maiúsculas e sem hífen: mesmo hash
    assert mfa._hash(mfa.normalize_code(codes[0].upper().replace("-", ""))) == hashes[0]


# ---------------------------------------------------------------- cifragem opcional do segredo

KEY = "k" * 32
USER = "6f1c2a4e-0000-4000-8000-000000000001"


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("DASH_TOTP_ENCRYPTION_KEY", KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_without_key_secret_is_stored_as_is(monkeypatch):
    monkeypatch.delenv("DASH_TOTP_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    assert mfa.seal("ABCDEF", USER) == "ABCDEF"
    assert mfa.unseal("ABCDEF", USER) == "ABCDEF"


def test_with_key_secret_roundtrips_and_is_bound_to_user(with_key):
    sealed = mfa.seal("ABCDEFGH", USER)
    assert sealed.startswith(mfa.ENC_PREFIX) and "ABCDEFGH" not in sealed
    assert mfa.seal("ABCDEFGH", USER) != sealed  # nonce novo a cada vez
    assert mfa.unseal(sealed, USER) == "ABCDEFGH"
    assert mfa.unseal("PLAINOLD", USER) == "PLAINOLD"  # legado em texto puro segue valendo
    with pytest.raises(KBError):
        mfa.unseal(sealed, "6f1c2a4e-0000-4000-8000-000000000002")  # copiado para outra conta


def test_wrong_or_missing_key_fails_closed(with_key, monkeypatch):
    sealed = mfa.seal("ABCDEFGH", USER)
    monkeypatch.setenv("DASH_TOTP_ENCRYPTION_KEY", "z" * 32)
    get_settings.cache_clear()
    with pytest.raises(KBError):
        mfa.unseal(sealed, USER)
    monkeypatch.delenv("DASH_TOTP_ENCRYPTION_KEY")
    get_settings.cache_clear()
    with pytest.raises(KBError):
        mfa.unseal(sealed, USER)


def test_short_key_is_refused_at_boot(monkeypatch):
    monkeypatch.setenv("DASH_TOTP_ENCRYPTION_KEY", "curta")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="DASH_TOTP_ENCRYPTION_KEY"):
            check_config()
    finally:
        get_settings.cache_clear()
