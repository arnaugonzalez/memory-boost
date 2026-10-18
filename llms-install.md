# Installing memory-boost (instructions for AI agents)

You are an AI coding agent (Claude Code, Codex, Cline, Cursor or similar) asked to install
memory-boost and register it as an MCP server. Follow these steps in order. Run each check; if one
fails, stop and report the command and its stderr. Ask the user before you edit a global config
file (anything under `~`) or write outside the memory home.

What it is: a markdown wiki that agents read and write over MCP (nine `memory_*` tools), plus
`drift` reports on that memory and `mine` digests of local Claude Code transcripts. Everything is
local files; nothing is sent anywhere. The optional semantic recall talks only to a local Ollama.

## 1. Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (`uv --version`). If uv is missing, ask the
  user before installing it. `pipx` works too: replace `uv tool install` with `pipx install`.
- Linux or macOS. Windows is untested.

## 2. Install the CLI (pinned)

```bash
uv tool install memory-boost==0.2.0
memory-boost --version          # expect: memory-boost 0.2.0
```

If `memory-boost` is not found, the uv tool bin directory is not on PATH: run `uv tool dir --bin`
and use that full path in the MCP config below, or ask the user before running `uv tool update-shell`.

Without installing anything permanently, use `uvx memory-boost==0.2.0 <command>` instead of
`memory-boost <command>` everywhere below (MCP config: command `uvx`, args
`["--quiet", "memory-boost==0.2.0", "serve"]`).

## 3. Create the memory home

```bash
memory-boost init               # creates the wiki in $MEMORY_BOOST_HOME (default ~/.local/share/memory-boost)
memory-boost drift              # exit 0; an empty wiki gives an empty report
```

To see what it does on sample data first: `memory-boost init --example` (only into an empty home).
To use an existing markdown wiki, set `MEMORY_BOOST_WIKI=/path/to/wiki` in the MCP server's `env`.

## 4. Register the MCP server

Server name: `memory`. Transport: stdio (default). Command: `memory-boost`, args: `["serve"]`.
Pick the block for your harness.

**Claude Code.** If the user wants the full setup (MCP server, session-start hook, skills), tell
them to run `/plugin marketplace add arnaugonzalez/memory-boost` and then
`/plugin install memory-boost@memory-boost` themselves, and skip the rest of this step.
MCP server only:

```bash
claude mcp add memory -s user -- memory-boost serve      # all projects (global: ask first)
claude mcp add memory -s project -- memory-boost serve   # this repo only: writes ./.mcp.json
claude mcp list                                          # expect: memory: memory-boost serve - ✓ Connected
```

**Codex CLI**:

```bash
codex mcp add memory -- memory-boost serve
```

or in `~/.codex/config.toml`:

```toml
[mcp_servers.memory]
command = "memory-boost"
args = ["serve"]
```

**Cline** (`cline_mcp_settings.json`), **Cursor** (`.cursor/mcp.json`), **VS Code, Gemini CLI and
other JSON configs**:

```json
{
  "mcpServers": {
    "memory": { "command": "memory-boost", "args": ["serve"] }
  }
}
```

### Optional: one shared server over HTTP

For several sessions sharing one long-lived server, run streamable HTTP (stateless, JSON responses).
It binds to 127.0.0.1 and has no authentication: never expose it on another interface.

```bash
memory-boost serve --http                 # defaults: --host 127.0.0.1 --port 8765, endpoint /mcp
claude mcp add --transport http memory -s user http://127.0.0.1:8765/mcp
```

JSON configs: `{"mcpServers": {"memory": {"type": "streamableHttp", "url": "http://127.0.0.1:8765/mcp"}}}`
(Cline uses `streamableHttp`; Claude Code's `.mcp.json` uses `"type": "http"`). Codex:
`url = "http://127.0.0.1:8765/mcp"` under `[mcp_servers.memory]`. The server must already be
running (a user service or a terminal); this step does not start it for the user.

## 5. Check that the server answers

This talks to the server directly, independent of any harness. Expect `9`:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | memory-boost serve 2>/dev/null | grep -o '"name":"memory_[a-z]*"' | sort -u | wc -l
```

For the HTTP server: `curl -s -X POST http://127.0.0.1:8765/mcp -H 'Content-Type: application/json'
-H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"0"}}}'`
returns `"serverInfo":{"name":"memory-boost","version":"0.2.0"}`.

The MCP tools: `memory_brief`, `memory_recall`, `memory_page`, `memory_save`, `memory_checkpoint`,
`memory_resume`, `memory_drift`, `memory_lesson`, `memory_shelf`.

## 6. Tell the agent to use it

Add one line to the project's agent instructions (`AGENTS.md`, `CLAUDE.md`, `.clinerules`, Cursor
rules), after asking the user:

> At session start call `memory_brief` (MCP server `memory`) and follow it. Record decisions with
> `memory_save`; before `/compact` or a long pause, call `memory_checkpoint`.

Claude Code without the plugin can also inject the brief automatically with a SessionStart hook in
`~/.claude/settings.json` (global: ask first):

```json
{ "hooks": { "SessionStart": [ { "hooks": [ { "type": "command", "command": "memory-boost hook session-start" } ] } ] } }
```

## 7. Report back

Tell the user: the installed version, where the memory home is, which config file you changed and
the exact entry, the result of the check in step 5, and the first lines of `memory-boost drift`.

## Uninstall

`uv tool uninstall memory-boost`, remove the `memory` entry from the MCP config, and delete the
memory home only if the user asks (it holds their notes).
