"""Reflect: synthesize repeated setup friction and drops into portable lesson drafts.

Inspired by Hindsight (retain / recall / reflect): `memory_save` retains events,
`brief`/`recall` retrieves them, and `reflect` distills repeated cross-session patterns
into deterministic lesson drafts (without mandatory LLM calls). Dry-run by default;
pass `--apply` to persist drafts via `lessons.save_lesson()`.
"""
from __future__ import annotations

import re

from memory_boost import core, friction, lessons


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "lesson")[:48].strip("-")


def _tags_for_projects(projects: list[str]) -> list[str]:
    tags: set[str] = set()
    for p in projects:
        tags |= lessons.project_tags(p)
    if tags:
        return sorted(tags)
    return sorted({core.norm_project(p) for p in projects if p and p != "?"})


def _covered_by_existing(cause: str, title: str, existing: list[dict]) -> str | None:
    slug = _slug(cause)
    norm_cause = core.norm_project(cause)
    norm_title = core.norm_project(title)
    for item in existing:
        ex_name = core.norm_project(item.get("name", ""))
        ex_title = core.norm_project(item.get("title", ""))
        if ex_name == slug or ex_name == norm_cause or norm_cause in ex_name or norm_cause in ex_title or norm_title == ex_title:
            return item.get("name")
    return None


def _collect_drops(since: str | None) -> list[dict]:
    groups: dict[str, dict] = {}
    for ev in core.iter_events():
        drops = ev.get("drops")
        if not isinstance(drops, list):
            continue
        day = friction._day(ev)
        if since and day < since:
            continue
        session = (ev.get("session") or {}).get("id") or f"{ev.get('project')}@{ev.get('ts')}"
        proj = ev.get("project", "?")
        for d in drops:
            if isinstance(d, dict):
                raw = d.get("reason") or d.get("what") or d.get("text") or d.get("id") or ""
            else:
                raw = str(d or "")
            key = re.sub(r"\s+", " ", raw.strip())
            if not key:
                continue
            g = groups.setdefault(key, {"pattern": key, "sessions": set(), "projects": set(), "last": ""})
            g["sessions"].add(session)
            g["projects"].add(proj)
            g["last"] = max(g["last"], day)
    ranked = sorted(groups.values(), key=lambda g: (len(g["sessions"]), len(g["projects"]), g["last"]), reverse=True)
    return [{"pattern": g["pattern"], "sessions": len(g["sessions"]), "projects": sorted(g["projects"]), "last": g["last"]}
            for g in ranked]


def reflect_candidates(since: str | None = None, min_sessions: int = 2, min_projects: int = 2) -> dict:
    """Distill repeated friction causes and drop patterns into lesson drafts."""
    fric = friction.report(since=since, top=50)
    existing = lessons.all_lessons()
    candidates: list[dict] = []
    skipped_existing: list[dict] = []
    seen_slugs: set[str] = set()

    for p in fric["proposals"]:
        has_fix = bool(p.get("fixes"))
        if len(p["projects"]) < min_projects and not (p["sessions"] >= min_sessions and has_fix):
            continue
        cause = p["cause"]
        name = _slug(cause)
        title = p["fixes"][0] if has_fix else f"Prevent recurring friction: {cause}"
        matched = _covered_by_existing(cause, title, existing)
        if matched:
            skipped_existing.append({"cause": cause, "lesson": matched})
            continue
        if name in seen_slugs:
            continue
        seen_slugs.add(name)
        projs = [x for x in p["projects"] if x and x != "?"]
        applies = _tags_for_projects(projs) or ["general"]
        body_lines = [f"Pattern observed in {p['sessions']} sessions across {', '.join(projs) or 'unknown'}: {cause}.", ""]
        if p.get("examples"):
            body_lines.append("Observed friction:")
            body_lines.extend(f"- {ex}" for ex in p["examples"])
            body_lines.append("")
        if p.get("fixes"):
            body_lines.append("Recommended fix:")
            body_lines.extend(f"- {fx}" for fx in p["fixes"])
        candidates.append({
            "source": "friction",
            "cause": cause,
            "name": name,
            "title": core._one_line(title),
            "applies_when": applies,
            "learned_on": core.norm_project(projs[0]) if projs else None,
            "cue": f"When friction or setup workaround resembles: {cause}",
            "evidence": f"seen in {p['sessions']} sessions across {len(projs)} projects (last {p['last']})",
            "confidence": "medium",
            "sessions": p["sessions"],
            "projects": projs,
            "body": "\n".join(body_lines).strip() + "\n",
        })

    for d in _collect_drops(since):
        if len(d["projects"]) < min_projects and d["sessions"] < min_sessions:
            continue
        pat = d["pattern"]
        name = _slug(f"drop-{pat}")
        title = f"Resolve recurring drop trigger: {pat}"
        matched = _covered_by_existing(pat, title, existing)
        if matched or name in seen_slugs:
            if matched:
                skipped_existing.append({"cause": pat, "lesson": matched})
            continue
        seen_slugs.add(name)
        projs = [x for x in d["projects"] if x and x != "?"]
        applies = _tags_for_projects(projs) or ["general"]
        candidates.append({
            "source": "drops",
            "cause": pat,
            "name": name,
            "title": core._one_line(title),
            "applies_when": applies,
            "learned_on": core.norm_project(projs[0]) if projs else None,
            "cue": f"When a task stalls or drops on: {pat}",
            "evidence": f"seen in {d['sessions']} sessions across {len(projs)} projects (last {d['last']})",
            "confidence": "medium",
            "sessions": d["sessions"],
            "projects": projs,
            "body": f"Repeated task drop observed ({d['sessions']} sessions, {', '.join(projs)}): {pat}.\n",
        })

    return {
        "since": since,
        "min_sessions": min_sessions,
        "min_projects": min_projects,
        "signals": fric["signals"],
        "candidates": candidates,
        "skipped_existing": skipped_existing,
        "applied": [],
    }


def apply_candidates(res: dict) -> list[str]:
    saved: list[str] = []
    for c in res["candidates"]:
        p = lessons.save_lesson(
            name=c["name"],
            title=c["title"],
            applies_when=c["applies_when"],
            body=c["body"],
            learned_on=c.get("learned_on"),
            cue=c.get("cue"),
            evidence=c.get("evidence"),
            confidence=c.get("confidence"),
        )
        saved.append(str(p))
    res["applied"] = saved
    return saved


def format_reflection(res: dict) -> str:
    lines = [
        f"# Reflect since {res['since'] or 'the beginning'}",
        f"{core.plural(res['signals'], 'signal')} · **{core.plural(len(res['candidates']), 'candidate')}**"
        + (f" · {len(res['skipped_existing'])} already covered" if res["skipped_existing"] else ""),
        "",
    ]
    if not res["candidates"]:
        lines.append("No new cross-session lesson candidates found.")
    for i, c in enumerate(res["candidates"], 1):
        lines.append(f"## {i}. {c['title']} (`{c['name']}`)")
        lines.append(f"- source: {c['source']} · applies_when: [{', '.join(c['applies_when'])}]")
        lines.append(f"- evidence: {c['evidence']}")
        lines.append(f"- cue: {c['cue']}")
        lines.append("")
    if res.get("applied"):
        lines.append("Applied lessons:")
        lines.extend(f"- {path}" for path in res["applied"])
    return "\n".join(lines).rstrip() + "\n"
