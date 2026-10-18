# Contributing

```bash
uv sync
uv run pytest
uvx ruff check .
```

Keep the core stdlib-only (the `mcp` package is the single runtime dependency) and keep
the hook path fast: `memory-boost hook` must not import `mcp`. Open an issue before
large changes.

Releasing: the version lives in `pyproject.toml`, `src/memory_boost/__init__.py`,
`.claude-plugin/plugin.json` (`version` and both `memory-boost==X` launchers), `server.json`,
the `memory-boost==X` pins in `README.md` and `llms-install.md`, and a dated `CHANGELOG.md`
heading. `tests/test_packaging.py` fails until all of them agree.
