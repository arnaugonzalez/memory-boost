# Security

Please report vulnerabilities privately through GitHub's "Report a vulnerability"
(Security tab) rather than a public issue.

## What memory-boost does and does not do

- No network: the package opens no sockets and makes no HTTP calls. The MCP server talks to
  your agent over stdin/stdout only.
- No command execution: nothing is run through a shell or `subprocess`.
- Files: the MCP tools read and write only inside `MEMORY_BOOST_HOME`; names coming from the
  model (project, agent, page) are validated as slugs, so `../` cannot escape it.
- `mine` reads your local agent transcripts and prints aggregates only: tool counts, the first
  word of failed commands, file paths with your home shortened to `~`. It stores nothing.

## Trust boundary: the wiki is instructions

The session-start hook and `memory_brief` put wiki text straight into your agent's context.
Whatever is written there, your agent reads as guidance. Only point `MEMORY_BOOST_WIKI` at a
wiki you or your own agents wrote; review pages that came from elsewhere before using them,
exactly as you would a `CLAUDE.md` or `AGENTS.md`. Do not store secrets in it.

## How releases are made

Releases are built and published by GitHub Actions from a version tag, using PyPI Trusted
Publishing (no long-lived token exists), after a manual approval on the `pypi` environment.
Actions are pinned to commit SHAs and audited with zizmor; dependencies are checked with
pip-audit before every release.
