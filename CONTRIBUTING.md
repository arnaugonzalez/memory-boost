# Contributing

```bash
uv sync
uv run pytest
uvx ruff check .
```

Keep the core stdlib-only (the `mcp` package is the single runtime dependency) and keep
the hook path fast: `memory-boost hook` must not import `mcp`. Open an issue before
large changes.
