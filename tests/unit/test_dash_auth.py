from mcp_rag_api.core.dash_auth import hash_password, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("senha forte aqui")
    assert stored.startswith("pbkdf2$210000$")
    assert verify_password("senha forte aqui", stored)
    assert not verify_password("senha errada", stored)


def test_password_hash_uses_random_salt():
    a = hash_password("mesma senha")
    b = hash_password("mesma senha")
    assert a != b
    assert verify_password("mesma senha", a)
    assert verify_password("mesma senha", b)


def test_verify_rejects_garbage_stored_value():
    assert not verify_password("x", "")
    assert not verify_password("x", "formato-invalido")
    assert not verify_password("x", "pbkdf2$abc$zz$yy")
