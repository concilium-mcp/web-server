import pytest

from mcp_rag_api.security import KBError, PermissionDenied, Principal, bearer_token, validate_scopes


def test_scope_implications():
    writer = Principal(actor="a", scopes=frozenset({"write"}))
    assert writer.has("read") and writer.has("write")
    assert not writer.has("agents:manage")
    admin = Principal(actor="b", scopes=frozenset({"admin"}))
    assert admin.has("agents:manage") and admin.is_manager
    with pytest.raises(PermissionDenied):
        writer.require("agents:manage")


def test_collection_restriction():
    p = Principal(actor="a", scopes=frozenset({"read"}), allowed_collections=("manuais",))
    assert p.collection_filter(None) == ["manuais"]
    assert p.collection_filter(["manuais"]) == ["manuais"]
    with pytest.raises(PermissionDenied):
        p.collection_filter(["financeiro"])
    unrestricted = Principal(actor="b", scopes=frozenset({"read"}))
    assert unrestricted.collection_filter(None) is None


def test_validate_scopes():
    assert validate_scopes(["write", "read", "read"]) == ["read", "write"]
    with pytest.raises(KBError):
        validate_scopes(["root"])


def test_bearer_token():
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer  abc ") == "abc"
    assert bearer_token("Basic abc") is None
    assert bearer_token(None) is None
