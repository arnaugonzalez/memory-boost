"""Drift report: what in the wiki is stale, silent or was never written down.

Reads only the wiki and the checkpoints; never the transcripts (see mine.py).
Each finding carries a `project` so brief() can inject the ones that matter
for the current session."""
from __future__ import annotations

import datetime as dt
import re

from memory_boost import core

SILENT_DAYS = 30            # a project page with no log entry for this long is "silent"
OPEN_CHECKPOINT_DAYS = 7    # an active checkpoint older than this was probably abandoned
BUSY_SESSIONS = 8           # log entries a project needs before "0 decisions" is suspicious
TAGS_RE = re.compile(r"[\w-]+")


def parse_tags(meta: dict, key: str = "tags") -> set[str]:
    return {core.norm_project(t) for t in TAGS_RE.findall(meta.get(key, ""))}


def _valid_date(s: str) -> bool:
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _days_between(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def drift_report(today: str | None = None) -> dict:
    """Return {"findings": [...], "totals": {...}}. Findings are dicts with
    kind, severity (1 = act now, 3 = fyi), project (or None), title, detail."""
    today = today or core._today()
    w = core.wiki_dir()
    findings: list[dict] = []

    # 1. decisions: past review_by, missing review_by, superseded without successor
    decisions = sorted((w / "decisions").glob("*.md"))
    for p in decisions:
        meta, body = core.parse_frontmatter(p.read_text(encoding="utf-8"))
        tags = parse_tags(meta)
        project = next((t for t in tags if (w / "projects" / f"{t}.md").exists()), None)
        status = core._status(meta)
        review_by = meta.get("review_by")
        if review_by and not _valid_date(review_by):
            findings.append({"kind": "decision-bad-date", "severity": 2, "project": project,
                             "title": p.stem, "detail": f"review_by {review_by!r} is not YYYY-MM-DD"})
            review_by = None
        if status in (None, "current") and review_by and review_by < today:
            findings.append({"kind": "decision-expired", "severity": 1, "project": project,
                             "title": p.stem, "detail": f"review_by {review_by} "
                             f"({_days_between(review_by, today)} days ago); revisit or mark historic"})
        elif status in (None, "current") and not review_by:
            findings.append({"kind": "decision-no-review", "severity": 3, "project": project,
                             "title": p.stem, "detail": "no review_by: it will never come up for review"})
        if (status == "superseded" or core.SUPERSEDED_RE.search(body)) and not meta.get("superseded_by"):
            findings.append({"kind": "decision-superseded-orphan", "severity": 2, "project": project,
                             "title": p.stem,
                             "detail": "marked superseded but no superseded_by: points nowhere"})

    # 2. projects: silent pages, busy projects with no recorded decisions
    entries = [e for e in core.parse_log(w / "log.md") if not e.get("malformed")]
    last_log: dict[str, str] = {}
    count: dict[str, int] = {}
    for e in entries:
        last_log[e["project"]] = max(last_log.get(e["project"], ""), e["date"])
        count[e["project"]] = count.get(e["project"], 0) + 1
    decided: set[str] = set()
    for p in decisions:
        meta, _ = core.parse_frontmatter(p.read_text(encoding="utf-8"))
        decided |= parse_tags(meta)
    projects = [p.stem for p in sorted((w / "projects").glob("*.md")) if not core._is_history(p.stem)]
    for pr in projects:
        last = last_log.get(pr)
        if not last:
            findings.append({"kind": "project-silent", "severity": 3, "project": pr, "title": pr,
                             "detail": "has a page but never logged a session"})
        elif _days_between(last, today) > SILENT_DAYS:
            findings.append({"kind": "project-silent", "severity": 2, "project": pr, "title": pr,
                             "detail": f"last session {last} ({_days_between(last, today)} days ago); "
                             "still listed as live"})
        if count.get(pr, 0) >= BUSY_SESSIONS and pr not in decided:
            findings.append({"kind": "project-no-decisions", "severity": 2, "project": pr, "title": pr,
                             "detail": f"{count[pr]} sessions logged, 0 decisions tagged '{pr}': "
                             "the learning lives only in transcripts"})

    # 3. checkpoints left open
    open_ckpts = 0
    for c in core.list_checkpoints(include_done=True):
        if c.get("status") == "done":
            continue
        open_ckpts += 1
        age = _days_between(c["updated"][:10], today)
        if age > OPEN_CHECKPOINT_DAYS:
            findings.append({"kind": "checkpoint-open", "severity": 2, "project": c["project"],
                             "title": c["session_id"][:8], "detail": f"'{c['task'][:60]}' open for "
                             f"{age} days; finish it or mark done"})

    findings.sort(key=lambda f: (f["severity"], f["kind"], f["title"]))
    totals = {"decisions": len(decisions), "projects": len(projects), "log_entries": len(entries),
              "open_checkpoints": open_ckpts, "findings": len(findings)}
    return {"today": today, "findings": findings, "totals": totals}


def for_project(project: str, n: int = 3, today: str | None = None) -> list[str]:
    """The n most urgent findings for one project, as bullet lines (for brief())."""
    slug = core.norm_project(project)
    hits = [f for f in drift_report(today)["findings"] if f["project"] == slug]
    return [f"- {f['kind']}: {f['title']} — {f['detail']}" for f in hits[:n]]


def format_report(report: dict) -> str:
    t = report["totals"]
    lines = [f"# Drift report — {report['today']}",
             f"{core.plural(t['decisions'], 'decision')} · {core.plural(t['projects'], 'project')} · "
             f"{core.plural(t['log_entries'], 'log entry', 'log entries')} · "
             f"{core.plural(t['open_checkpoints'], 'open checkpoint')} · "
             f"**{core.plural(t['findings'], 'finding')}**", ""]
    by_kind: dict[str, list[dict]] = {}
    for f in report["findings"]:
        by_kind.setdefault(f["kind"], []).append(f)
    for kind, items in by_kind.items():
        lines.append(f"## {kind} ({len(items)})")
        lines += [f"- {f['title']}" + (f" [{f['project']}]" if f["project"] and f["project"] != f["title"]
                                        else "") + f" — {f['detail']}" for f in items]
        lines.append("")
    if not report["findings"]:
        lines.append("Nothing drifted. Either the wiki is tidy or it is empty.")
    else:
        lines.append(f"next: {next_command(report['findings'][0])}   # the most urgent finding")
    return "\n".join(lines).rstrip() + "\n"


def next_command(finding: dict) -> str:
    """The exact command an agent should run to act on a finding."""
    if finding["kind"] == "checkpoint-open":
        return f"memory-boost checkpoints --project {finding['project']}"
    if finding["kind"].startswith("decision"):
        return f"memory-boost page {finding['title']}"
    return f"memory-boost page {finding['project']}"
