# Concilium plugin for Claude Code

Starts every Claude Code session with a short **project brief** from your Concilium knowledge base: the note your team marked as the brief, where the rest of the knowledge lives (folders and note counts) and, optionally, the agent's last session and open tasks. Details come on demand through the Concilium MCP tools, so a new chat does not have to load a large `AGENTS.md` or `CLAUDE.md` every time.

## Install

```text
/plugin marketplace add concilium-mcp/web-server
/plugin install concilium@concilium
```

Then, in a repository:

```text
/concilium-init
```

It writes `.concilium.json` (safe to commit, no secrets):

```json
{ "url": "https://concilium.example.com", "collection": "MyProject", "agent": "myproject-dev" }
```

And set a **read-only** key of the project's agent in your environment:

```bash
export CONCILIUM_API_KEY=kb_sk_...
```

Mark the brief in the dashboard: open the note and click the pin button (**Use as project brief**). Keep it under ~800 tokens (`BRIEF_MAX_TOKENS` on the server; longer briefs are cut with a warning).

## How it works

- `hooks/session_start.py` runs on `SessionStart` (startup, `/clear` and after compaction). It looks for `.concilium.json` from the session folder upwards, calls `GET /context/brief?collection=…&agent=…` with a 3-second timeout and returns the text as additional context.
- It never blocks a session: no config, no key, a non-`https` URL (other than localhost), a server error or a timeout all end silently with one line on stderr.
- Requires `python3` on the `PATH` (standard library only).

- With an `agent` in `.concilium.json`, it also sends the repository name (for a git worktree, the main repository's name) and the Claude Code session id, so the brief lists **your cards in this repository** (held, stalled, waiting for review, assigned) and the cards other sessions are working on now. It tells the model which `session` to pass to the board tools.

- **Real token spend (0.3):** an async hook on `Stop` and `SessionEnd` reads the session transcript incrementally and sends only counters to `POST /usage/sessions`: API calls, input, output, cache write and cache read tokens, the model, and how many Concilium tool calls the session made (plus the estimated size of what they returned). No prompt, code or content leaves the machine. The dashboard overview shows the real spend per project, sessions with and without Concilium, and each board card shows the spend of the sessions that touched it.

Clients without hooks (claude.ai, ChatGPT, Cursor) can call the `get_project_brief` MCP tool instead, and `load_agent` already includes the brief of the agent's first allowed collection.
