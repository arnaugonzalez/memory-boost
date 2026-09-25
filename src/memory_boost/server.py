"""MCP server (stdio) exposing the memory as eight tools. Run: `memory-boost serve`."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from memory_boost import core, drift, lessons

mcp = FastMCP("memory")


@mcp.tool()
def memory_brief(project: str | None = None, budget: int = 1200) -> str:
    """Cheap orientation (~1k tokens): wiki catalog, the project's stable context and
    its latest sessions. Call it at the start of a session in harnesses without a
    SessionStart hook, or to reload a project's context mid-session. `project` is a
    project name or a directory path."""
    return core.brief(project, budget=budget)["text"]


@mcp.tool()
def memory_recall(query: str, project: str | None = None, limit: int = 8,
                  include_historic: bool = False) -> str:
    """Full-text search over wiki pages and log entries. Each hit is tagged
    [kind | date | freshness]; current knowledge is listed before historic and
    superseded. Historic hits are omitted unless include_historic=true."""
    results = core.recall(query, project=project, limit=limit, include_historic=include_historic)
    if not results:
        return "(no results)"
    lines = [f"[{r['kind']} | {r['date'] or '?'} | {r['freshness']}] {r['ref']} — {r['extract']}"
             for r in results]
    lines.append("\n> Read a whole section with memory_page(name, section)")
    return "\n".join(lines)


@mcp.tool()
def memory_page(name: str, section: str | None = None) -> str:
    """Read a wiki page by name (e.g. 'my-api'), or one section of it (a heading or
    the anchor returned by memory_recall). Prefer this over reading the whole file."""
    return core.get_page(name, section)


@mcp.tool()
def memory_save(project: str, action: str, detail: str | None = None, result: str = "ok",
                agent: str = "mcp", pending: str | None = None) -> str:
    """Record what was done in a session: one raw JSONL event plus one compiled entry
    in wiki/log.md (creates the project page if it does not exist yet). `action` is a
    one-line summary; `detail` a longer bullet; `pending` what is left to do.
    Does not commit anything to git."""
    slug = core.save(project, action, detail=detail, result=result, agent=agent, pending=pending)
    return f"saved: {slug} | {action}"


@mcp.tool()
def memory_checkpoint(project: str, session_id: str, task: str, done: list[str] | None = None,
                      next_step: str | None = None, files: list[str] | None = None,
                      notes: list[str] | None = None, status: str = "active",
                      plan_ref: str | None = None, agent: str = "agent") -> str:
    """Save this session's working state (a small JSON, no indexing) so it survives
    context compaction and parallel sessions on the same project can see it. Call it
    after each milestone: task = current goal (1 line), done = finished steps
    (accumulated), next_step = the concrete next step, files = files touched.
    session_id: the `checkpoint_session=` value injected at session start, or any
    stable id. Set status=done when the session ends (after memory_save)."""
    c = core.save_checkpoint(project, session_id, task, agent=agent, done=done,
                             next_step=next_step, files=files, notes=notes, status=status,
                             plan_ref=plan_ref)
    return f"checkpoint: {c['project']} | {session_id} | {c['status']} | done={len(c['done'])}"


@mcp.tool()
def memory_resume(project: str, session_id: str | None = None) -> str:
    """Return this session's checkpoint (if session_id is given) plus one line per
    other active session on the project. Use after a compaction or when picking up
    unfinished work; much cheaper than memory_recall."""
    return core.resume_text(project, session_id)


@mcp.tool()
def memory_drift(project: str | None = None) -> str:
    """What in the memory has drifted: decisions past review_by, superseded decisions
    without a successor, project pages with no session for weeks, busy projects with no
    recorded decisions, checkpoints left open. Whole wiki, or one project's findings."""
    report = drift.drift_report()
    if project:
        lines = drift.for_project(project, n=20)
        return "\n".join(lines) if lines else f"(nothing drifted for {core.norm_project(project)})"
    return drift.format_report(report)


@mcp.tool()
def memory_lesson(name: str, title: str, applies_when: list[str], body: str,
                  learned_on: str | None = None) -> str:
    """Record a lesson that should reach other projects: what worked (or failed), the
    tools involved and when it applies. `applies_when` are stack/situation tags
    (e.g. ["fastapi", "celery", "e2e-tests"]); projects whose page lists matching
    `tags:` get this lesson in their session brief. `learned_on` = this project's slug."""
    p = lessons.save_lesson(name, title, applies_when, body, learned_on=learned_on)
    return f"lesson saved: {p.name} (applies_when: {', '.join(sorted(set(applies_when)))})"


def main() -> None:
    mcp.run()
