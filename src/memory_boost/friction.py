"""Setup friction, grouped by cause: the weekly review over what sessions logged.

Sessions record `friction: [{what, kind, cause, fix?}]` in memory_save when the setup (not the
task) cost time, forced a guess or a workaround. This module folds those events into at most
`top` proposals. A cause seen once is noise, not a pattern: it is counted but never proposed,
so one bad session cannot rewrite the setup (Harness² treats a defect in 1/k rollouts the same
way). Reads only the event log; proposes, never edits."""
from __future__ import annotations

from memory_boost import core

MIN_REPEATS = 2             # sessions that must hit a cause before it becomes a proposal


def _day(ev: dict) -> str:
    return str(ev.get("date_local") or ev.get("ts", ""))[:10]


def report(since: str | None = None, project: str | None = None, top: int = 3) -> dict:
    slug = core.norm_project(project) if project else None
    groups: dict[str, dict] = {}
    signals = 0
    for ev in core.iter_events():
        if not isinstance(ev.get("friction"), list):
            continue
        if since and _day(ev) < since:
            continue
        if slug and ev.get("project") != slug:
            continue
        session = (ev.get("session") or {}).get("id") or f"{ev.get('project')}@{ev.get('ts')}"
        for f in ev["friction"]:
            if not isinstance(f, dict) or not f.get("cause"):
                continue
            signals += 1
            g = groups.setdefault(f["cause"], {"cause": f["cause"], "sessions": set(), "kinds": {},
                                               "projects": set(), "whats": [], "fixes": [],
                                               "last": ""})
            g["sessions"].add(session)
            g["kinds"][f.get("kind", "?")] = g["kinds"].get(f.get("kind", "?"), 0) + 1
            g["projects"].add(ev.get("project", "?"))
            g["last"] = max(g["last"], _day(ev))
            if f.get("what") and f["what"] not in g["whats"]:
                g["whats"].append(f["what"])
            if f.get("fix") and f["fix"] not in g["fixes"]:
                g["fixes"].append(f["fix"])
    ranked = sorted(groups.values(), key=lambda g: (len(g["sessions"]), len(g["projects"]), g["last"]),
                    reverse=True)
    out = [{"cause": g["cause"], "sessions": len(g["sessions"]), "projects": sorted(g["projects"]),
            "kinds": g["kinds"], "last": g["last"], "examples": g["whats"][:3], "fixes": g["fixes"][:3]}
           for g in ranked]
    proposals = [g for g in out if g["sessions"] >= MIN_REPEATS][:max(top, 0)]
    noise = [g["cause"] for g in out if g["sessions"] < MIN_REPEATS]
    return {"since": since, "project": slug, "signals": signals, "causes": len(out),
            "proposals": proposals, "noise": noise}


def format_report(r: dict) -> str:
    scope = f" for {r['project']}" if r["project"] else ""
    lines = [f"# Friction{scope} since {r['since'] or 'the beginning'}",
             f"{core.plural(r['signals'], 'signal')} · {core.plural(r['causes'], 'cause')} · "
             f"**{core.plural(len(r['proposals']), 'proposal')}**", ""]
    for i, p in enumerate(r["proposals"], 1):
        kinds = ", ".join(f"{k} {n}" for k, n in sorted(p["kinds"].items()))
        lines.append(f"## {i}. {p['cause']} — {p['sessions']} sessions, {', '.join(p['projects'])} "
                     f"({kinds}; last {p['last']})")
        lines += [f"- seen: {w}" for w in p["examples"]]
        lines += [f"- suggested fix: {f}" for f in p["fixes"]]
        lines.append("")
    if not r["proposals"]:
        lines.append("No cause repeats across sessions: nothing to change. Keeping the setup as it is "
                     "is a valid outcome.")
    if r["noise"]:
        lines.append(f"seen once (not proposed): {', '.join(r['noise'][:10])}")
    return "\n".join(lines).rstrip() + "\n"
