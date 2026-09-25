---
name: memory-boost
description: Router for the memory-boost CLI (markdown wiki for agents + drift reports + session digests). Use when the user asks what the memory knows, what is stale, what past sessions taught, or when you need to record a decision, checkpoint or lesson from the shell instead of MCP.
---

# memory-boost

A router, not a manual. The installed binary's help is the reference: run `memory-boost --help`
once per session and `memory-boost <cmd> --help` before any subcommand you have not used yet.

## Contract

- stdout = data, stderr = warnings/errors with a `fix:` line. Exit 0 = trust stdout,
  1 = not found / nothing to read, 2 = usage or invalid input. Never retry the same command on
  exit 2; read stderr and change the input.
- Every reading command takes `--json` (field `version` pins the schema).
- Reports end with a `next:` line: run that command before inventing your own.

## Routing

| Need | Command |
|---|---|
| Orient at session start (if the hook did not run) | `memory-boost brief --cwd .` |
| Find a page or decision | `memory-boost recall "<words>"` then `memory-boost page <slug>` |
| What in the memory is stale or contradictory | `memory-boost drift` (or `--project <slug>`) |
| How the user's sessions actually go (aggregates only) | `memory-boost mine --since 7d` |
| Lessons from other projects that apply here | `memory-boost lessons --project <slug>` |
| Record progress mid-session | `memory-boost checkpoint --project … --session … --task …` |
| Record a decision or session end | `memory-boost save --project … --action …` |
| Record a lesson that travels | `memory-boost lesson --name … --title … --applies-when … --body -` |

## Guardrails

- `mine` prints aggregates only; still, never paste transcript contents into the wiki or a report.
- Prefer the MCP tools (`memory_*`) when the server is registered; use the CLI for scripts,
  piping and `drift`/`mine`, which have no MCP equivalent beyond `memory_drift`.
- Do not edit `log.md` by hand; `save` appends it. Pages under `wiki/` are yours to edit.
- If the CLI is missing: stop and say so (`uv tool install memory-boost`). Do not reimplement it.

## Done when

The user's question is answered from wiki content, or the write command printed `saved:` /
`checkpoint:` / `lesson saved:`.
