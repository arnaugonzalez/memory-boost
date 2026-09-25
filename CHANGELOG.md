# Changelog

## 0.1.0 — 2026-09-26

First public release.

- Claude Code plugin (`/plugin install memory-boost@memory-boost`): MCP server, session-start
  hook and skills in one step. Listed in the official MCP registry as
  `io.github.arnaugonzalez/memory-boost`.
- Release pipeline hardened: actions pinned to SHAs, no default token permissions, no cache in
  release jobs, PyPI publish gated by manual approval; zizmor, pip-audit and bandit in preflight.
- Example wiki shows one finding of each drift kind; `MEMORY_BOOST_TODAY` pins the date for
  reproducible reports and tests.
- CLI contract for agents: stdout = data, stderr = `error:` + `fix:`, exit 0/1/2, `--json` on every
  reading command (with `version`), reports end with a `next:` command; `/memory-boost` router skill.
- MCP server (stdio) with `memory_brief`, `memory_recall`, `memory_page`, `memory_save`,
  `memory_checkpoint`, `memory_resume`, `memory_drift`, `memory_lesson`.
- Markdown wiki store under `MEMORY_BOOST_HOME`; SQLite FTS5 index refreshed incrementally on
  every recall.
- Freshness labels (current / unclassified / review / historic / superseded); decisions carry
  `status:` and `review_by:`.
- `memory-boost drift`: decisions past review, superseded without successor, silent projects,
  busy projects with no decisions, checkpoints left open. Top findings injected in the brief.
- `memory-boost mine`: aggregate digest of local Claude Code transcripts (tools, failed
  commands, re-reads, subagent fan-out, checkpoint discipline). Contents never leave the machine.
- `memory-boost lesson` / `lessons`: `concepts/` pages with `applies_when:` that reach other
  projects through their `tags:`; `/retro` skill to write them at session end.
- `memory-boost hook session-start` for Claude Code `SessionStart` (startup, resume, compact).
- `memory-boost init --example` seeds a small example wiki and four synthetic transcripts.
