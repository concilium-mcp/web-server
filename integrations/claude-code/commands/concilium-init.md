---
description: Connect this repository to a Concilium project (writes .concilium.json and offers a minimal CLAUDE.md)
---

Set this repository up so every new session starts with the project brief from Concilium.

1. Ask the user, one question at a time:
   - the Concilium URL (for example `https://concilium.example.com`; it must be `https`, or `http://localhost` for local tests);
   - the project, that is, the top-level folder (collection) in Concilium. If the Concilium MCP tools are connected, call `list_collections` and offer the names;
   - optionally, the agent slug for this project (for example `myproject-dev`), so the brief also brings the agent's last session and open tasks.
2. Write `.concilium.json` at the repository root with `url`, `collection` and, if given, `agent`. Never put a key in this file: it is meant to be committed.
3. Tell the user to set the `CONCILIUM_API_KEY` environment variable with a **read-only** key of that agent (dashboard → Agents → issue key). Never ask them to paste the key into the chat.
4. If the Concilium MCP tools are connected, call `get_project_brief` for that collection and show its size. If the project has no brief yet, explain how to mark one: open the note in the dashboard and click the pin button ("Use as project brief"). Aim for under 800 tokens.
5. Offer a minimal `CLAUDE.md`. If one exists, show what you would change and ask before editing; never delete existing content. Suggested text:

```markdown
# <Project>

The context of this project lives in Concilium (collection "<collection>"). A short brief is loaded when the session starts.
For details, use `search_knowledge` with `collections=["<collection>"]`, then `get_document` with `outline=true` and `section="..."`.
Facts about how the product behaves come from the code; Concilium holds context, decisions and plans.
```

6. Finish by listing what was created or changed.
