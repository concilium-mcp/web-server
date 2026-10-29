"""Regressões da auditoria de segurança de 2026-10-29 (plan-web-30) — sem banco."""

import time

import pytest
from pydantic import ValidationError

from mcp_rag_api.core import agents, dash_auth
from mcp_rag_api.core.file_import import MAX_DOCX_XML_BYTES, convert_file
from mcp_rag_api.core.obsidian_import import MAX_TAG_CHARS, MAX_TAGS, _frontmatter_tags, split_frontmatter
from mcp_rag_api.dashboard_api import LoginIn
from mcp_rag_api.security import KBError, PermissionDenied, Principal

# ---------------------------------------------------------------- tags do frontmatter


def _alias_bomb(levels: int) -> str:
    lines = ["---", 'a0: &a0 ["xxxxxxxxx","xxxxxxxxx","xxxxxxxxx","xxxxxxxxx","xxxxxxxxx"]']
    for n in range(1, levels):
        lines.append(f"a{n}: &a{n} [" + ",".join([f"*a{n - 1}"] * 9) + "]")
    lines += [f"tags: [ok, *a{levels - 1}]", "---", "corpo"]
    return "\n".join(lines)


def test_alias_lists_in_tags_are_dropped_instead_of_expanded():
    fm, _ = split_frontmatter(_alias_bomb(10))  # str() pediria ~10^9 caracteres
    started = time.monotonic()
    assert _frontmatter_tags(fm["tags"]) == ["ok"]
    assert time.monotonic() - started < 1


def test_tags_keep_scalars_and_respect_count_and_length_caps():
    assert _frontmatter_tags(["a", 2, 1.5, True, None, {"x": 1}, ["y"]]) == ["a", "2", "1.5"]
    many = _frontmatter_tags([f"t{n}" for n in range(MAX_TAGS * 3)])
    assert len(many) == MAX_TAGS
    assert _frontmatter_tags(["x" * (MAX_TAG_CHARS * 5)]) == ["x" * MAX_TAG_CHARS]
    assert len(_frontmatter_tags(" ".join(f"s{n}" for n in range(MAX_TAGS * 3)))) == MAX_TAGS


def test_markdown_upload_with_alias_bomb_converts_with_safe_tags():
    converted = convert_file("bomba.md", _alias_bomb(10).encode())
    assert converted.tags == ["ok"]
    assert converted.content == "corpo"


# ---------------------------------------------------------------- .docx descomprimido


def test_docx_with_too_much_xml_is_rejected_before_parsing(make_docx, docx_p):
    paragraph = docx_p("a")
    # comprimido de verdade, um XML assim cabe em poucos KB: o limite de upload não pegaria
    data = make_docx(paragraph * (MAX_DOCX_XML_BYTES // len(paragraph) + 1))
    with pytest.raises(KBError, match="grande demais"):
        convert_file("bomba.docx", data)


def test_docx_within_budget_still_converts(make_docx, docx_p):
    assert convert_file("ok.docx", make_docx(docx_p("Olá mundo"))).content == "Olá mundo"


# ---------------------------------------------------------------- rate limit do login


def test_limiter_state_is_fixed_size_and_capped(monkeypatch):
    monkeypatch.setattr(dash_auth, "LOGIN_LIMITER_PRUNE_AT", 50)
    monkeypatch.setattr(dash_auth, "LOGIN_LIMITER_MAX_ENTRIES", 100)
    limiter = dash_auth.LoginRateLimiter(max_failures=1, base_seconds=60)
    for n in range(500):
        limiter.register_failure((f"{n:06d}" + "x" * 100_000, "203.0.113.7" * 50))
    assert len(limiter._failures) <= 100
    assert all(len(user) == 64 and len(ip) <= 64 for user, ip in limiter._failures)


def test_limiter_still_blocks_and_resets_by_username():
    limiter = dash_auth.LoginRateLimiter(max_failures=1, base_seconds=60)
    key = ("u" * 1000, "*")
    limiter.register_failure(key)
    assert limiter.blocked_for(key) > 0
    assert limiter.blocked_for(("outro", "*")) == 0
    limiter.reset(key)
    assert limiter.blocked_for(key) == 0


def test_login_body_rejects_oversized_username_and_password():
    LoginIn(username="u" * 254, password="p" * 1024)
    for body in ({"username": "u" * 255, "password": "p"}, {"username": "u", "password": "p" * 1025}):
        with pytest.raises(ValidationError):
            LoginIn(**body)


# ---------------------------------------------------------------- agentes


async def test_read_only_agent_cannot_propose_profile_update():
    reader = Principal(actor="agent:leitor", scopes=frozenset({"read"}), agent_slug="leitor")
    with pytest.raises(PermissionDenied):  # antes de tocar no banco
        await agents.propose_agent_update(reader, "nota", system_prompt="injetado")


class _FakeConn:
    def __init__(self, row: dict) -> None:
        self.row = row

    async def fetchrow(self, query: str, slug: str) -> dict | None:
        return self.row if slug == self.row["slug"] else None


ADMIN_AGENT = {"slug": "ops", "scopes": ["admin"], "allowed_collections": []}
UNSCOPED_AGENT = {"slug": "geral", "scopes": ["read"], "allowed_collections": []}
MANAGER = Principal(actor="key:m", scopes=frozenset({"read", "write", "agents:manage"}))
SCOPED_MANAGER = Principal(
    actor="key:sm", scopes=frozenset({"read", "write", "agents:manage"}), allowed_collections=("a",)
)


async def test_resolve_agent_requires_reach_over_other_agents():
    with pytest.raises(PermissionDenied):
        await agents.resolve_agent(_FakeConn(ADMIN_AGENT), MANAGER, "ops")
    with pytest.raises(PermissionDenied):
        await agents.resolve_agent(_FakeConn(UNSCOPED_AGENT), SCOPED_MANAGER, "geral")
    assert (await agents.resolve_agent(_FakeConn(UNSCOPED_AGENT), MANAGER, "geral"))["slug"] == "geral"
    admin = Principal(actor="key:a", scopes=frozenset({"admin"}))
    assert (await agents.resolve_agent(_FakeConn(ADMIN_AGENT), admin, "ops"))["slug"] == "ops"


async def test_resolve_agent_self_access_is_unchanged():
    me = Principal(actor="agent:ops", scopes=frozenset({"read", "write"}), agent_slug="ops")
    assert (await agents.resolve_agent(_FakeConn(ADMIN_AGENT), me, None))["slug"] == "ops"
    with pytest.raises(PermissionDenied):
        await agents.resolve_agent(_FakeConn(UNSCOPED_AGENT), me, "geral")
