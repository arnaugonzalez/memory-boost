# Changelog

## unreleased

- `drift` and recall freshness: an explicit `status:` on a decision now wins over
  superseded-sounding words in its prose ("ya no aplica", "desactualizada"), which raised
  false `decision-superseded-orphan` findings.

## 0.2.1 — 2026-11-08

- Lessons carry optional `cue`, `evidence` and `confidence` (high | medium | low; only with evidence), after Harness² (arXiv 2610.14224). `memory_lesson` and `memory-boost lesson --cue --evidence --confidence`. The brief marks lessons without evidence as `unproven`.
- `memory_save` v2 field `friction`: `[{what, kind: time | guess | workaround, cause, fix?}]`, setup friction the next agent would also hit. `memory-boost friction [--since 7d] [--project] [--top 3] [--json]` groups it by cause; a cause seen in one session is reported as noise, never proposed.
- Drift: `lesson-unproven` (a lesson with no evidence) and `project-no-tags` (a project page no lesson can reach, reported only when lessons exist). `totals` gains `lessons`.
- Slugs fold accents instead of dropping the letter: `Validar código` → `validar-codigo` (was `validar-c-digo`), so tags and names written in Spanish, Catalan, French… match again.
- Pending `owner`, `money` and `stated` accept only the English values; the Spanish aliases from the first local v2 build are gone.
- sdist is an explicit allowlist (`only-include`): local run logs and planning notes can no longer slip into a release.
- `retro` skill: cue, evidence and confidence for each lesson; no lesson is a valid outcome; `friction` in the closing save.
- `memory-boost hook stop` and `hook pre-compact`: automatic checkpoint from the transcript, no LLM. Prompts (with `<private>…</private>` stripped), files edited and git commits go under the checkpoint's `auto` key; task, done, next and notes written by the agent are never overwritten. Stop writes only when something changed and 10 min (`MEMORY_BOOST_AUTOSAVE_SECS`) or 4 prompts (`MEMORY_BOOST_AUTOSAVE_TURNS`) passed; PreCompact always writes, so the post-compact re-injection has something to show. A one-question session leaves no checkpoint (Stop waits for an edit, a commit or 4 prompts), and `drift` does not count auto-only checkpoints as open work.
- Brief: a line longer than its budget (the project catalog in `### Index`) is cut at a word instead of dropped, so the index no longer shows up empty.

- README: social preview on top; new narrated demo (`docs/demo.tape`) in an "Example" section; AGENTS.md for coding agents.
- `memory_save` v2 optional fields: `outcome` (done | progress | blocked | waiting | research | abandoned), `pending` as a list of `{text, owner, severity, due?, waiting_on?}`, `closes`, `drops`, `decisions`, `deadlines`, `orgs`, `touches`, `uses`, `billable`, `money`, `stated`, plus the external `session` and `metrics` blocks. v1 calls keep working unchanged; the log.md header does not change.
- `pending` is now persisted in the JSONL with stable ids `<project>#<n>` assigned by the server (counter per project in `events/pending-seq.json`). A plain string still works and is split on `;` as before.
- `closes` and `drops` fold over the events: open pending items are the ones created and not closed or dropped later. Nothing is edited in place.
- `date_local` (the local day of the save) and `MEMORY_BOOST_TZ` (IANA zone, UTC by default) decide the log.md header date for v2 events.
- `closes` and `drops` only record ids that are open: unknown, already closed or repeated ids are left out of the event and reported as `ignored: <id> (<why>)` in the save line, so a typo cannot fake a close.
- Pending items accept an optional `done_check` (a command or an observable condition). memory-boost stores it and shows it in `pending` (`check: …`); it never runs it.
- Brief: a new `### <project> — open pending` block lists the open items with their ids, so a later session can cite them in `closes`.
- CLI: `memory-boost save --outcome`, `--closes`, `--session-json`, `--metrics-json` and `--fields-json` (any v2 field as one JSON object, or `@file`); `memory-boost pending [--project] [--json]` prints the open items.

## 0.2.0 — 2026-10-18

- recall: freshness is now a down-weight on the bm25 score instead of a hard sort, so a strong match is no longer buried under fresher weak ones.
- Optional semantic recall: `pip install "memory-boost[semantic]"` plus `MEMORY_BOOST_SEMANTIC=1` and a local
  `embeddinggemma` (Ollama) adds vector search via sqlite-vec. Default mode when on is `hybrid`; `recall --mode`
  and `MEMORY_BOOST_RECALL_MODE` select `fts`, `semantic` or `hybrid`. Falls back to full-text if Ollama is down.
  The base install is unchanged.
  While embedding, `memory-boost index` and `recall` report `embedding N/M chunks…` on stderr (one
  rewritten line on a terminal, a line every 128 chunks otherwise); stdout and the MCP server stay quiet.
- `shelf`: park a one-line idea in a wiki page (default `concepts/idea-shelf.md`) from the CLI
  (`memory-boost shelf "..."`) or the new `memory_shelf` MCP tool, without reading the page.
- `serve --http [--host 127.0.0.1] [--port 8765]`: the MCP server over streamable HTTP (stateless,
  JSON responses) at `http://HOST:PORT/mcp`, for one long-lived local server shared by several
  sessions. stdio stays the default. Requires `mcp>=1.10`.
- MCP `initialize` reports `serverInfo` as `memory-boost` and its own version (it reported the mcp
  library's version).
- Plugin: the MCP server and the session-start hook run the exact release (`uvx memory-boost==0.2.0`)
  instead of the plugin checkout, as the Claude plugin directory requires pinned launchers. A test
  fails if any pinned install (plugin, README, `llms-install.md`) drifts from `__version__`.
- `llms-install.md`: steps an agent (Claude Code, Codex, Cline, Cursor) can follow to install the CLI
  and register the MCP server over stdio or `serve --http`, with a harness-independent check. The
  README gains an "Install from your agent" prompt and pins its install commands.
- `docs/logo-400.png` (400×400, for MCP directories), rendered by `scripts/render-logo.py`.

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
