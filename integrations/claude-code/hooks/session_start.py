#!/usr/bin/env python3
"""Hook SessionStart do plugin Concilium para o Claude Code (plan-web-34).

Procura `.concilium.json` subindo a partir da pasta da sessão, busca o resumo do projeto em
`GET <url>/context/brief` e o devolve como contexto adicional da sessão. Só biblioteca padrão.

Quadros (plan-web-37a): manda junto o repositório git da pasta (nome da pasta do repositório; num
worktree, o do repositório principal) e o id da sessão do Claude Code, para o resumo trazer "seus
cartões neste repositório"; e diz ao modelo qual `session` passar nas tools dos cartões.

Nunca atrapalha a sessão: sem arquivo, sem chave, servidor fora do ar ou resposta inesperada, sai
com código 0 e no máximo uma linha no stderr.

.concilium.json (pode ir para o git, não tem segredo):
    {"url": "https://concilium.example.com", "collection": "Pronfi", "agent": "pronfi-dev"}
A chave vem da variável CONCILIUM_API_KEY (ou da que `key_env` indicar). Use uma chave só de
leitura do agente do projeto, nunca uma chave admin.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_NAME = ".concilium.json"
TIMEOUT_SECONDS = 3.0
USER_AGENT = "concilium-claude-code-plugin/0.2"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def log(message: str) -> None:
    print(f"concilium: {message}", file=sys.stderr)


def find_config(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        candidate = folder / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def find_repo(start: Path) -> str | None:
    """Nome do repositório git da pasta. Worktree: o `.git` é um arquivo "gitdir: <repo>/.git/worktrees/<x>"."""
    for folder in (start, *start.parents):
        marker = folder / ".git"
        if marker.is_dir():
            return folder.name
        if marker.is_file():
            try:
                line = marker.read_text(encoding="utf-8").strip()
            except OSError:
                return folder.name
            gitdir = Path(line.removeprefix("gitdir:").strip())
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                return gitdir.parent.parent.parent.name
            return folder.name
    return None


def build_request(
    config: dict, env: dict, repo: str | None = None, session: str | None = None
) -> urllib.request.Request | None:
    url = str(config.get("url") or env.get("CONCILIUM_URL") or "").rstrip("/")
    collection = config.get("collection")
    key = env.get(str(config.get("key_env") or "CONCILIUM_API_KEY"))
    if not url or not collection:
        log(f"{CONFIG_NAME} precisa de 'url' e 'collection'.")
        return None
    if not key:
        log("defina CONCILIUM_API_KEY (chave de leitura do agente do projeto) para carregar o resumo.")
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS):
        log("a url precisa ser https (http só para localhost): a chave não pode trafegar aberta.")
        return None
    query = {"collection": collection}
    if config.get("agent"):
        query["agent"] = config["agent"]
        if repo:
            query["repo"] = repo
        if session:
            query["session"] = session
    return urllib.request.Request(
        f"{url}/context/brief?{urllib.parse.urlencode(query)}",
        headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    start = Path(payload.get("cwd") or os.getcwd())
    config_path = find_config(start)
    if config_path is None:
        return 0
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"não consegui ler {config_path}: {e}")
        return 0
    session = str(payload.get("session_id") or "") or None
    request = build_request(config, dict(os.environ), find_repo(start), session)
    if request is None:
        return 0
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.load(response)
        text = data["text"]
    except urllib.error.HTTPError as e:
        log(f"o servidor respondeu {e.code} ao pedir o resumo do projeto.")
        return 0
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as e:
        log(f"resumo do projeto indisponível ({e.__class__.__name__}).")
        return 0
    if session and config.get("agent"):
        text += (
            f'\n\nSessão do Concilium: passe session="{session}" em start_card, update_card, check_item, '
            "log_card, release_card, submit_card e finish_card."
        )
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
