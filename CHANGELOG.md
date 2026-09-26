# Changelog

## 0.1.1 — 2026-09-26

- README renders on PyPI and in the MCP registry: the demo GIF, the architecture diagram (now an
  image, source in `docs/architecture.mmd`) and the security link use absolute URLs. A test keeps
  relative links out.
- Plugin: the first session no longer prints uv's download progress (`uvx --quiet`).

## 0.1.0 — 2026-09-26

First public release.

What you get:

- `memory-boost drift`: what in your agents' memory expired, points nowhere, went silent or was
  never written down. The current project's top findings go into every session brief.
- `memory-boost mine`: a digest of your local Claude Code transcripts (tools, failed commands,
  re-reads, subagent fan-out, checkpoint discipline). Aggregates only; contents never leave the
  machine.
- `memory-boost lessons` and the `/retro` skill: lessons learned in one project reach every
  project with the same stack.
- Claude Code plugin (`/plugin install memory-boost@memory-boost`): MCP server, session-start
  brief and skills in one step. Also in the official MCP registry as
  `io.github.arnaugonzalez/memory-boost`.
- MCP server (stdio) with eight tools: `memory_brief`, `memory_recall`, `memory_page`,
  `memory_save`, `memory_checkpoint`, `memory_resume`, `memory_drift`, `memory_lesson`.
- A markdown wiki you own, with freshness labels (current / review / historic / superseded) and
  `review_by:` dates on decisions; SQLite FTS5 search refreshed on every recall.
- Session checkpoints that survive `/compact` and show other active sessions on the project.
- `memory-boost init --example`: a small wiki with one finding of each drift kind and four
  synthetic transcripts to try everything without your own data.

For agents and maintainers:

- CLI contract: stdout = data, stderr = `error:` + `fix:`, exit 0/1/2, `--json` on every reading
  command (with `version`), reports end with a `next:` command; `/memory-boost` router skill.
- `MEMORY_BOOST_TODAY` pins the date for reproducible reports.
- Release pipeline: actions pinned to SHAs, minimal token permissions, PyPI Trusted Publishing
  behind a manual approval; zizmor, pip-audit and bandit before every release.
