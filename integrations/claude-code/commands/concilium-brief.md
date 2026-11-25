---
description: Draft or refresh this project's brief in Concilium from the repository (you approve before anything is saved)
---

Write the project brief: the short note Concilium loads at the start of every session in this repository.

1. Find the project. Read `.concilium.json` from the current folder upwards and take `collection` (and `url`). If there is none, ask the user to run `/concilium-init` first and stop.
2. This needs the Concilium MCP tools (`get_project_brief`, `search_knowledge`, `get_document`, `add_document`, `update_document`, `set_project_brief`). If they are not connected, you can still write the draft, but say that it has to be saved and marked in the dashboard by hand (step 7).
3. Call `get_project_brief` for that collection.
   - With a brief (`brief_document_id` set): read that note with `get_document`. You are **refreshing** it: keep what is still true, fix what the code now contradicts, add what is missing, remove what is stale. Say up front what you plan to change.
   - Without a brief: you are **creating** one.
4. Read what the repository already says: `README*`, `CLAUDE.md`, `AGENTS.md`, the top-level folder layout, the package/build files and `git log --oneline -20`. Call `search_knowledge` with `collections=["<collection>"]` so the brief points to the notes that already exist instead of repeating them. Facts about how the product behaves come from the code, not from old notes.
5. Write the draft under ~800 tokens (the server cuts longer briefs at `BRIEF_MAX_TOKENS`), in the language the project's notes use:
   - what the project is and who it is for;
   - stack, and how to run and test it;
   - where each part lives (folders, services, repositories);
   - the rules a new session must not break;
   - where the detailed notes are in Concilium (titles or folders).

   Leave out secrets, keys, customer data, and anything that changes every week (progress, open bugs: those belong on the board).
6. Show the full draft (when refreshing, also a short list of what changed) and ask the user to approve or edit it. **Do not save before they say yes.**
7. Save:
   - Refreshing: `update_document` on the same note, with a short `change_note`. The note stays marked as the brief.
   - Creating: `add_document` in that collection (title "Resumo do projeto", or the equivalent in the notes' language), then `set_project_brief` with the new `document_id`.
   - If writing is refused (the key is read-only) or the MCP tools are missing, hand the text to the user: create the note in the dashboard, open it and click the pin button ("Use as project brief").
8. Call `get_project_brief` again and report the size (`tokens_estimate`) and whether it was cut (`truncated`).
