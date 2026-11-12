"""Hook de gasto real do plugin (plan-web-38): leitura incremental do transcript e envio do acumulado."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "integrations" / "claude-code" / "hooks"
sys.path.insert(0, str(HOOKS))
import report_usage as ru  # noqa: E402


def _assistant(mid, usage, content=(), model="claude-opus-5-5", ts="2026-11-12T10:00:00Z"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "version": "2.1.322",
        "message": {"id": mid, "model": model, "usage": usage, "content": list(content)},
    }


def _transcript(tmp_path: Path, lines: list[dict], partial: str = "") -> Path:
    p = tmp_path / "t.jsonl"
    p.write_text("".join(json.dumps(x) + "\n" for x in lines) + partial)
    return p


def _fresh():
    return {"offset": 0, "seen": [], "tools": [], "totals": dict.fromkeys(ru.COUNTERS, 0)}


def test_scan_counts_each_message_once_and_concilium_results(tmp_path):
    u = {
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 50000,
    }
    tool = {"type": "tool_use", "id": "tu1", "name": "mcp__claude_ai_Concilium__search_knowledge"}
    other = {"type": "tool_use", "id": "tu2", "name": "Bash"}
    lines = [
        _assistant("m1", u, [tool]),
        _assistant("m1", u, [other]),  # mesma mensagem em outro bloco: usage repetido não conta de novo
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": "x" * 400}]}
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "tu2", "content": "y" * 4000}]},
        },
        _assistant(
            "m2",
            {**u, "output_tokens": 7},
            [{"type": "tool_use", "id": "tu3", "name": "mcp__kb-pronfi-dev__get_document"}],
            ts="2026-11-12T10:05:00Z",
        ),
    ]
    t = _transcript(tmp_path, lines, partial='{"type": "assistant", "mess')
    s = ru.scan(t, _fresh())
    assert s["totals"] == {
        "requests": 2,
        "input_tokens": 20,
        "output_tokens": 107,
        "cache_write_tokens": 2000,
        "cache_read_tokens": 100000,
        "concilium_calls": 2,
        "concilium_result_tokens": 100,
    }
    assert s["model"] == "claude-opus-5-5" and s["client"] == "claude-code 2.1.322"
    assert s["started_at"] == "2026-11-12T10:00:00Z" and s["ended_at"] == "2026-11-12T10:05:00Z"
    # linha pela metade fica para depois; a segunda leitura não reconta nada
    assert s["offset"] < t.stat().st_size
    again = ru.scan(t, s)
    assert again["totals"]["requests"] == 2


class _Recv(BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        _Recv.bodies.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(self.rfile.read(n))}
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    _Recv.bodies = []
    httpd = HTTPServer(("127.0.0.1", 0), _Recv)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_hook_sends_cumulative_totals_once(tmp_path, server):
    (tmp_path / ".concilium.json").write_text(
        json.dumps({"url": server, "collection": "Pronfi", "agent": "pronfi-dev"})
    )
    t = _transcript(tmp_path, [_assistant("m1", {"input_tokens": 5, "output_tokens": 50})])
    payload = json.dumps({"session_id": "sessao-de-teste-1", "transcript_path": str(t), "cwd": str(tmp_path)})
    env = {"PATH": "/usr/bin:/bin", "CONCILIUM_API_KEY": "kb_sk_x", "XDG_CACHE_HOME": str(tmp_path / "cache")}
    for _ in range(2):  # sem mudança no transcript, o segundo Stop não reenvia
        out = subprocess.run(
            [sys.executable, str(HOOKS / "report_usage.py")],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert out.returncode == 0, out.stderr
    assert len(_Recv.bodies) == 1
    sent = _Recv.bodies[0]
    assert sent["path"] == "/usage/sessions" and sent["auth"] == "Bearer kb_sk_x"
    assert (
        sent["body"]["collection"] == "Pronfi" and sent["body"]["output_tokens"] == 50 and sent["body"]["requests"] == 1
    )
    assert "content" not in json.dumps(sent["body"])  # só contadores
