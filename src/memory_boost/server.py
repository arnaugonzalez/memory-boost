"""MCP server exposing the memory as nine tools. Run: `memory-boost serve` (stdio) or
`memory-boost serve --http` (streamable HTTP, stateless, JSON responses)."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from memory_boost import __version__, core, drift, lessons

mcp = FastMCP("memory-boost")
# FastMCP has no version argument; without this, initialize reports the mcp library's version.
mcp._mcp_server.version = __version__


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
                agent: str = "mcp", pending: str | list[dict] | None = None,
                session: dict | None = None, metrics: dict | None = None,
                outcome: str | None = None, closes: list[str] | None = None,
                drops: list[dict] | None = None, decisions: list[dict] | None = None,
                deadlines: list[dict] | None = None, orgs: list[str] | None = None,
                touches: list[str] | None = None, uses: list[str] | None = None,
                billable: bool | None = None, money: list[dict] | None = None,
                stated: list[dict] | None = None, friction: list[dict] | None = None) -> str:
    """Record what was done in a session: one raw JSONL event plus one compiled entry in
    wiki/log.md (creates the project page if it does not exist yet). Does not commit to git.

    action: one line, at most 120 characters. detail: a self-contained paragraph, at most
    600 characters; the next session's brief reads it.
    outcome: done | progress | blocked | waiting | research | abandoned.
    pending: what is left to do. Either a string split on ';' (each part is the user's,
    severity 3), or a list of {text, owner, severity, due?, waiting_on?, done_check?} where owner is
    user | agent | client | third_party, severity is 1-3, due is YYYY-MM-DD, and waiting_on
    is org:<slug> or person:<slug> when the owner is client or third_party. Severity 1: production
    down or data loss, a hard deadline within 72 hours, or a client waiting on something
    that depends on the user. Severity 2: a known open bug, a deadline within 7 days, or an
    unmitigated risk (exposed secret, work unpushed for more than 3 days). Severity 3: the rest.
    done_check: how to tell it is finished (a command, or an observable condition); stored and
    shown, never run. The server assigns each item an id <project>#<n>; the brief shows open
    items with their ids.
    closes: ids of open pending items (as shown in the brief) that this session finished. Ids that
    are unknown, already closed or repeated are not written and come back as `ignored` in the reply.
    drops: [{id, why}] open pending items that no longer apply.
    decisions: [{slug, new}] pages in wiki/decisions created or cited. deadlines: [{what, at,
    hard, pending_ref?}]. orgs: organisations involved that are not the project's client
    (prospects, suppliers). touches: other project slugs affected. uses: "bip:<capability>"
    (background IP reused) or "proj:<slug>". billable: whether it counts toward billing a client.
    money: [{kind, status, ref, amount?, currency?}] with kind quote | invoice | payment |
    cost | price, status draft | sent | accepted | paid | rejected, and ref the path
    of the document. stated: [{what, quote, about?}] only with a literal quote from the user,
    what = energy | mood | worry | load. Never infer the user's mood.
    friction: [{what, kind, cause, fix?}] things in the setup (not the task) that cost time,
    forced a guess or a workaround, with kind = time | guess | workaround and cause a short
    stable slug (e.g. "missing-env-var") so repeats group. Only log one the NEXT agent would
    also hit; a one-off slip is noise. `memory-boost friction` groups them by cause.
    session and metrics are opaque blocks produced by an external ledger; pass them only when
    that ledger produced them."""
    out = core.save_full(project, action, detail=detail, result=result, agent=agent,
                         pending=pending, session=session, metrics=metrics, outcome=outcome,
                         closes=closes, drops=drops, decisions=decisions, deadlines=deadlines,
                         orgs=orgs, touches=touches, uses=uses, billable=billable, money=money,
                         stated=stated, friction=friction)
    return core.save_summary(action, out)


@mcp.tool()
def memory_shelf(text: str, to: str = "concepts/idea-shelf", project: str = "") -> str:
    """Append a dated one-line idea to a wiki page without reading it."""
    out = core.shelf(text, page=to, project=project or None)
    return f"shelved: {out['page']} | {out['line']}"


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
                  learned_on: str | None = None, cue: str | None = None,
                  evidence: str | None = None, confidence: str | None = None) -> str:
    """Record a lesson that should reach other projects: what worked (or failed), the
    tools involved and when it applies. `applies_when` are stack/situation tags
    (e.g. ["fastapi", "celery", "e2e-tests"]); projects whose page lists matching
    `tags:` get this lesson in their session brief. `learned_on` = this project's slug.
    cue: the shape of situation it fires on (the pattern, not this task's facts).
    evidence: the observed contrast it rests on (before/after, A vs B, with numbers or the
    moment it diverged); "it looked like good practice" is not evidence. confidence:
    high | medium | low, only with evidence. If nothing observed supports a transferable
    lesson, do not record one: no lesson is a valid outcome."""
    p = lessons.save_lesson(name, title, applies_when, body, learned_on=learned_on,
                            cue=cue, evidence=evidence, confidence=confidence)
    return f"lesson saved: {p.name} (applies_when: {', '.join(sorted(set(applies_when)))})"


def main(http: bool = False, host: str = "127.0.0.1", port: int = 8765) -> None:
    if not http:
        mcp.run()
        return
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.run(transport="streamable-http")
