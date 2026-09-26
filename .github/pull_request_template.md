**What and why** (one or two lines):

**How I checked it**
- [ ] `uv run pytest` and `uvx ruff check .` pass
- [ ] If output changed: `memory-boost drift` / `mine` on the example data still read well
- [ ] If it touches `mine`: the digest still prints aggregates only (no prompts, commands or paths outside `~`)
