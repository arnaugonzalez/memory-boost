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
- `cue`: the *shape* of situation it fires on, not this task's facts. Too generic never
  changes behaviour ("be thorough"); too specific never fires again ("the fee rose on Jan 1").
- `evidence`: the observed contrast it rests on: before/after, A vs B, numbers, or the moment
  the run diverged. "It looked like good practice" is not evidence. If several things changed
  at once, the cause is confounded: narrow the lesson to what the record shows.
- `confidence`: high | medium | low, and only with `evidence`.

Skip anything that is only true for this codebase; that belongs in the project page.
**No lesson is a valid outcome.** A guessed lesson reaches every other project and misleads
them; leave it out rather than invent the attribution.

## 2. Decisions with a review date (memory_save + edit the decision page)

If a decision was made this session, write it to `decisions/<slug>.md` with `review_by:` (a date
when it should be questioned again) and `tags:` including the project slug. A decision without
`review_by` never comes back for review, and `memory_drift` will flag it.

## 3. Close the loop

- `memory_save(project, action, detail, pending, friction)` — one log entry for the session.
  `friction` lists what in the *setup* (not the task) cost time, forced a guess or a workaround:
  `[{what, kind: time|guess|workaround, cause: <stable-slug>, fix?}]`. Only add one the next
  agent would also hit; reuse an existing `cause` slug so repeats group in
  `memory-boost friction`.
- `memory_checkpoint(..., status="done")` — so the checkpoint stops showing as open.
- If `memory_drift(project)` lists anything for this project, fix what takes under a minute
  (mark a decision historic, add a `superseded_by`) and mention the rest in `pending`.
