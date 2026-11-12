#!/usr/bin/env python3
"""Hook Stop/SessionEnd do plugin Concilium (plan-web-38): gasto real de tokens da sessão.

Lê o transcript da sessão (JSONL do Claude Code) de onde parou da última vez e soma, uma vez por mensagem
(`message.id`), o que a API cobrou: entrada, saída, gravação e leitura de cache, nº de chamadas e modelo.
Conta as tools do Concilium (nome com "concilium" ou "kb-") e estima os tokens do que elas devolveram
(caracteres / 4). Manda o acumulado para POST <url>/usage/sessions. Nada de prompt, código ou conteúdo.

Estado em ~/.cache/concilium/usage/<sessão>.json (ponto do arquivo, ids já contados, totais). Nunca
atrapalha a sessão: qualquer erro termina com código 0 e uma linha no stderr. Só biblioteca padrão.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_start import CONFIG_NAME, LOCAL_HOSTS, find_config, find_repo, log  # noqa: E402

TIMEOUT_SECONDS = 4.0
USER_AGENT = "concilium-claude-code-plugin/0.3"
CONCILIUM_TOOL = re.compile(r"concilium|(^|__)kb-", re.IGNORECASE)
COUNTERS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "concilium_calls",
    "concilium_result_tokens",
)


def state_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "concilium" / "usage"


def load_state(session_id: str) -> dict:
    try:
        return json.loads((state_dir() / f"{session_id}.json").read_text())
    except (OSError, ValueError):
        return {"offset": 0, "seen": [], "tools": [], "totals": dict.fromkeys(COUNTERS, 0), "sent": None}


def save_state(session_id: str, state: dict) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{session_id}.json.tmp"
    tmp.write_text(json.dumps(state))
    tmp.replace(d / f"{session_id}.json")


def _text_len(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(c.get("text", "")) for c in content if isinstance(c, dict))
    return 0


def scan(path: Path, state: dict) -> dict:
    """Soma as linhas novas do transcript (só as completas) e devolve o estado atualizado."""
    seen = set(state["seen"])
    tools = set(state["tools"])
    totals = state["totals"]
    with path.open("rb") as f:
        f.seek(state["offset"])
        data = f.read()
    end = data.rfind(b"\n") + 1  # a última linha pode estar pela metade: fica para a próxima vez
    for raw in data[:end].splitlines():
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        ts = o.get("timestamp")
        if ts:
            state.setdefault("started_at", ts)
            state["ended_at"] = ts
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        if o.get("type") == "assistant":
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and CONCILIUM_TOOL.search(c.get("name", "")):
                    if c.get("id") not in tools:
                        tools.add(c.get("id"))
                        totals["concilium_calls"] += 1
            u = msg.get("usage")
            mid = msg.get("id")
            if u and mid and mid not in seen:
                seen.add(mid)
                totals["requests"] += 1
                totals["input_tokens"] += u.get("input_tokens") or 0
                totals["output_tokens"] += u.get("output_tokens") or 0
                totals["cache_write_tokens"] += u.get("cache_creation_input_tokens") or 0
                totals["cache_read_tokens"] += u.get("cache_read_input_tokens") or 0
                if msg.get("model"):
                    state["model"] = msg["model"]
        elif o.get("type") == "user":
            content = msg.get("content") if isinstance(msg.get("content"), list) else []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in tools:
                    totals["concilium_result_tokens"] += _text_len(c.get("content")) // 4
        if o.get("version"):
            state["client"] = f"claude-code {o['version']}"
    state["offset"] += end
    state["seen"] = list(seen)
    state["tools"] = list(tools)
    return state


def send(config: dict, env: dict, body: dict) -> bool:
    url = str(config.get("url") or env.get("CONCILIUM_URL") or "").rstrip("/")
    key = env.get(str(config.get("key_env") or "CONCILIUM_API_KEY"))
    if not url or not key:
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS):
        log("a url precisa ser https (http só para localhost).")
        return False
    req = urllib.request.Request(
        f"{url}/usage/sessions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as e:
        log(f"o servidor respondeu {e.code} ao receber o uso da sessão.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"uso da sessão não enviado ({e.__class__.__name__}).")
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    session_id = str(payload.get("session_id") or "")
    transcript = payload.get("transcript_path")
    if not session_id or not transcript or not Path(transcript).is_file():
        return 0
    start = Path(payload.get("cwd") or os.getcwd())
    config_path = find_config(start)
    if config_path is None:
        return 0
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"não consegui ler {CONFIG_NAME}: {e}")
        return 0
    try:
        state = scan(Path(transcript), load_state(session_id))
    except OSError as e:
        log(f"não consegui ler o transcript ({e.__class__.__name__}).")
        return 0
    body = {
        "session_id": session_id,
        "collection": config.get("collection"),
        "agent": config.get("agent"),
        "client": state.get("client"),
        "model": state.get("model"),
        "repo": find_repo(start),
        "started_at": state.get("started_at"),
        "ended_at": state.get("ended_at"),
        **state["totals"],
    }
    fingerprint = json.dumps(state["totals"], sort_keys=True)
    if fingerprint != state.get("sent") and send(config, dict(os.environ), body):
        state["sent"] = fingerprint
    save_state(session_id, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
