---
name: retro
description: End-of-session retrospective that turns what happened into memory other projects can use. Use when the user says "retro", "what did we learn", "save the lessons", or at the end of a session where something worked unusually well or failed in a way worth remembering.
---

# retro

Goal: leave the memory *better* than you found it, in three moves. Do not narrate; write.

## 1. Lessons that travel (memory_lesson)

Look back over this session for anything a different project would benefit from: a technique
that worked, a tool that saved time, a failure mode with a clear fix. For each one:

- `name`: short slug (`retry-jobs-idempotently`).
- `title`: one line, imperative ("Make background jobs idempotent before adding retries").
- `applies_when`: stack/situation tags where it applies (`["background-jobs", "queues"]`).
  Use the same words projects put in their `tags:` — that is how the lesson finds them.
- `body`: what happened, what fixed it, which tools or commands helped. 5-10 lines.
- `learned_on`: this project's slug.

Skip anything that is only true for this codebase; that belongs in the project page.

## 2. Decisions with a review date (memory_save + edit the decision page)

If a decision was made this session, write it to `decisions/<slug>.md` with `review_by:` (a date
when it should be questioned again) and `tags:` including the project slug. A decision without
`review_by` never comes back for review, and `memory_drift` will flag it.

## 3. Close the loop

- `memory_save(project, action, detail, pending)` — one log entry for the session.
- `memory_checkpoint(..., status="done")` — so the checkpoint stops showing as open.
- If `memory_drift(project)` lists anything for this project, fix what takes under a minute
  (mark a decision historic, add a `superseded_by`) and mention the rest in `pending`.
