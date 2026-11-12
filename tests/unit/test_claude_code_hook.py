"""Hook SessionStart do plugin do Claude Code (plan-web-34): nunca trava a sessão."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "integrations" / "claude-code" / "hooks" / "session_start.py"


class _Handler(BaseHTTPRequestHandler):
    status = 200
    seen: list[dict] = []

    def do_GET(self):  # noqa: N802 - nome exigido pelo http.server
        _Handler.seen.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "ua": self.headers.get("User-Agent")}
        )
        body = json.dumps({"text": "# Projeto X — resumo"}).encode()
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.status = 200
    _Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _run(cwd: Path, env: dict, session_id: str | None = None) -> subprocess.CompletedProcess:
    payload = {"cwd": str(cwd), "hook_event_name": "SessionStart"}
    if session_id:
        payload["session_id"] = session_id
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
        timeout=15,
    )


def _repo(tmp_path: Path, config: dict) -> Path:
    (tmp_path / ".concilium.json").write_text(json.dumps(config), encoding="utf-8")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    return sub  # a sessão abre numa subpasta: o hook sobe até achar o arquivo


def test_injects_brief_from_parent_config(tmp_path, server):
    cwd = _repo(tmp_path, {"url": server, "collection": "Projeto X", "agent": "x-dev"})
    out = _run(cwd, {"CONCILIUM_API_KEY": "kb_sk_test"})
    assert out.returncode == 0
    data = json.loads(out.stdout)
    assert data["hookSpecificOutput"] == {"hookEventName": "SessionStart", "additionalContext": "# Projeto X — resumo"}
    [req] = _Handler.seen
    assert req["path"] == "/context/brief?collection=Projeto+X&agent=x-dev"
    assert req["auth"] == "Bearer kb_sk_test" and req["ua"].startswith("concilium-claude-code-plugin/")


def test_no_config_is_silent(tmp_path):
    out = _run(tmp_path, {"CONCILIUM_API_KEY": "kb_sk_test"})
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_missing_key_and_server_error_do_not_block(tmp_path, server):
    cwd = _repo(tmp_path, {"url": server, "collection": "Projeto X"})
    out = _run(cwd, {})
    assert out.returncode == 0 and out.stdout == "" and "CONCILIUM_API_KEY" in out.stderr
    _Handler.status = 401
    out = _run(cwd, {"CONCILIUM_API_KEY": "kb_sk_bad"})
    assert out.returncode == 0 and out.stdout == "" and "401" in out.stderr


def test_refuses_plain_http_to_remote_host(tmp_path):
    cwd = _repo(tmp_path, {"url": "http://concilium.example.com", "collection": "X"})
    out = _run(cwd, {"CONCILIUM_API_KEY": "kb_sk_test"})
    assert out.returncode == 0 and out.stdout == "" and "https" in out.stderr


def test_unreachable_server(tmp_path):
    cwd = _repo(tmp_path, {"url": "http://127.0.0.1:9", "collection": "X"})
    out = _run(cwd, {"CONCILIUM_API_KEY": "kb_sk_test"})
    assert out.returncode == 0 and out.stdout == "" and "indisponível" in out.stderr


def test_sends_repo_and_session_for_cards(tmp_path, server):
    # worktree: o .git é um arquivo que aponta para <repo principal>/.git/worktrees/<nome>
    (tmp_path / "web-server" / ".git" / "worktrees" / "wt-x").mkdir(parents=True)
    wt = tmp_path / "worktrees" / "web-server-plan-x"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {tmp_path}/web-server/.git/worktrees/wt-x\n", encoding="utf-8")
    (tmp_path / ".concilium.json").write_text(
        json.dumps({"url": server, "collection": "Concilium", "agent": "concilium-dev"}), encoding="utf-8"
    )
    out = _run(wt, {"CONCILIUM_API_KEY": "kb_sk_test"}, session_id="abc-123")
    assert out.returncode == 0
    [req] = _Handler.seen
    assert req["path"] == "/context/brief?collection=Concilium&agent=concilium-dev&repo=web-server&session=abc-123"
    text = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("# Projeto X — resumo") and 'session="abc-123"' in text
