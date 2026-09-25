---
name: restore-session
description: Load context from the shared agent memory before continuing work on a project - latest sessions, pending items, open checkpoints. Use when the user says "restore session", "where were we", "catch me up" or starts work on a project without a SessionStart hook.
---

# restore-session

1. Call `memory_brief(project=<cwd or project name>)`.
2. If a previous session of yours may be unfinished, call `memory_resume(project, session_id)`.
3. For anything the brief only hints at, use `memory_recall(query, project)` and then
   `memory_page(name, section)` for the relevant section. Do not read whole pages by default.
4. Reply with a short "where we left off": last actions, pending items, active parallel
   sessions (so you don't step on their files), and the next concrete step you propose.
