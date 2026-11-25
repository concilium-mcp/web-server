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

## Commands

| Command | What it does |
|---|---|
| `/concilium-init` | Connects the repository to a Concilium project: asks for the URL, the project (collection) and, optionally, the agent; writes `.concilium.json`; offers a minimal `CLAUDE.md`. If the project has no brief yet, offers to run `/concilium-brief`. |
| `/concilium-brief` | Creates or refreshes the project brief. It reads the repository (README, `CLAUDE.md`/`AGENTS.md`, folder layout, build files, `git log`) and the notes already in the project, writes a draft under ~800 tokens, shows it to you and only saves after you approve: a new note marked with the `set_project_brief` MCP tool, or an update of the current brief. |

### Who writes the brief?

You decide. `/concilium-brief` drafts it and you approve, or you write the note yourself and mark it in the dashboard (open the note, click the pin button **Use as project brief**). Either way nothing is saved without a person. Keep it under ~800 tokens (`BRIEF_MAX_TOKENS` on the server; longer briefs are cut with a warning). `/concilium-brief` needs the Concilium MCP connected with a key that can write; the `CONCILIUM_API_KEY` of the hook stays read-only. When the project has no brief, or it has not been edited in 60 days, the session-start text tells the model to offer `/concilium-brief`.

## How it works

- `hooks/session_start.py` runs on `SessionStart` (startup, `/clear` and after compaction). It looks for `.concilium.json` from the session folder upwards, calls `GET /context/brief?collection=…&agent=…` with a 3-second timeout and returns the text as additional context.
- It never blocks a session: no config, no key, a non-`https` URL (other than localhost), a server error or a timeout all end silently with one line on stderr.
- Requires `python3` on the `PATH` (standard library only).

- With an `agent` in `.concilium.json`, it also sends the repository name (for a git worktree, the main repository's name) and the Claude Code session id, so the brief lists **your cards in this repository** (held, stalled, waiting for review, assigned) and the cards other sessions are working on now. It tells the model which `session` to pass to the board tools.

- **Real token spend (0.3):** an async hook on `Stop` and `SessionEnd` reads the session transcript incrementally and sends only counters to `POST /usage/sessions`: API calls, input, output, cache write and cache read tokens, the model, and how many Concilium tool calls the session made (plus the estimated size of what they returned). No prompt, code or content leaves the machine. The dashboard overview shows the real spend per project, sessions with and without Concilium, and each board card shows the spend of the sessions that touched it.

- **Leaner tool catalog:** connect the project MCP as `https://<your-concilium>/mcp?tools=dev`. It lists only the 23 tools a coding agent uses every day (search and read the knowledge base, update notes, memory and session, work on board cards) instead of the full catalog. Claude Code already loads MCP tools on demand, so the gain there is small (~500 tokens per session in our measurement); it matters more for clients that send every tool definition up front. Hidden tools still work if called.

Clients without hooks (claude.ai, ChatGPT, Cursor) can call the `get_project_brief` MCP tool instead, and `load_agent` already includes the brief of the agent's first allowed collection.
