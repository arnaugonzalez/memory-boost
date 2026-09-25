"""Mine local agent transcripts for how you actually work.

Reads Claude Code session files (~/.claude/projects/<cwd-slug>/<session>.jsonl)
and keeps only aggregates: tool counts, failed commands, re-reads, subagent
fan-out, checkpoint discipline. Prompt and tool *contents* are never stored
or printed, so the digest is safe to share."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from memory_boost.core import _today, plural

MIN_TURNS = 20          # shorter sessions are one-shot questions, not work sessions
LONG_SESSION = 60       # from here on a checkpoint is expected
REREAD_MIN = 4          # a file read this many times in one session is worth naming


def transcript_roots() -> list[Path]:
    if env := os.environ.get("MEMORY_BOOST_TRANSCRIPTS"):
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    return [Path.home() / ".claude" / "projects"]


def _empty(path: Path) -> dict:
    return {"file": path.name, "project": None, "turns": 0, "first": None, "last": None,
            "tools": Counter(), "bash_failed": 0, "failed_cmds": Counter(), "reads": Counter(),
            "checkpoints": 0, "saves": 0, "agents": 0}


def scan_session(path: Path) -> dict:
    """One JSONL transcript -> aggregate counters. Tolerates broken lines."""
    s = _empty(path)
    pending: dict[str, tuple[str, str]] = {}   # tool_use_id -> (tool name, first word of command)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if cwd := ev.get("cwd"):
                s["project"] = s["project"] or Path(cwd).name
            if ts := ev.get("timestamp"):
                s["first"] = s["first"] or ts
                s["last"] = ts
            content = (ev.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            if ev.get("type") == "assistant":
                s["turns"] += 1
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    name, inp = c.get("name", "?"), c.get("input") or {}
                    s["tools"][name] += 1
                    cmd = (inp.get("command") or "").split()
                    pending[c.get("id", "")] = (name, cmd[0] if cmd else "?")
                    if name == "Read":
                        s["reads"][inp.get("file_path", "")] += 1
                    elif name == "Agent":
                        s["agents"] += 1
                    elif name.endswith("memory_checkpoint"):
                        s["checkpoints"] += 1
                    elif name.endswith("memory_save"):
                        s["saves"] += 1
                elif c.get("type") == "tool_result" and c.get("is_error"):
                    name, cmd = pending.get(c.get("tool_use_id", ""), ("?", "?"))
                    if name == "Bash":
                        s["bash_failed"] += 1
                        s["failed_cmds"][cmd] += 1
    return s


def scan(roots: list[Path] | None = None, since: str | None = None,
         min_turns: int = MIN_TURNS) -> list[dict]:
    """All sessions under the roots with at least min_turns assistant turns,
    optionally only those started on/after `since` (YYYY-MM-DD)."""
    out = []
    for root in roots or transcript_roots():
        for f in sorted(root.glob("*/*.jsonl")) if root.exists() else ():
            s = scan_session(f)
            if s["turns"] < min_turns:
                continue
            if since and (s["first"] or "")[:10] < since:
                continue
            out.append(s)
    return out


def aggregate(sessions: list[dict]) -> dict:
    tools: Counter = Counter()
    failed: Counter = Counter()
    rereads: Counter = Counter()
    per_project: dict[str, dict] = {}
    reread_total = 0
    for s in sessions:
        tools.update(s["tools"])
        failed.update(s["failed_cmds"])
        for f, n in s["reads"].items():
            if n > 1:
                reread_total += n - 1
            if n >= REREAD_MIN:
                rereads[f] += n
        p = per_project.setdefault(s["project"] or "?", {"sessions": 0, "turns": 0, "bash_failed": 0})
        p["sessions"] += 1
        p["turns"] += s["turns"]
        p["bash_failed"] += s["bash_failed"]
    long = [s for s in sessions if s["turns"] >= LONG_SESSION]
    edits = tools["Edit"] + tools["Write"]
    extremes = sorted(((s["tools"]["Bash"] / max(s["tools"]["Edit"] + s["tools"]["Write"], 1),
                        s["turns"], s["project"] or "?", s["file"][:8]) for s in sessions), reverse=True)
    return {
        "sessions": len(sessions),
        "tools": dict(tools.most_common(10)),
        "bash": {"calls": tools["Bash"], "failed": sum(s["bash_failed"] for s in sessions),
                 "top_failed_cmds": dict(failed.most_common(5))},
        "bash_per_edit": round(tools["Bash"] / max(edits, 1), 1),
        "extreme_sessions": [{"ratio": round(r, 1), "turns": t, "project": p, "session": f}
                             for r, t, p, f in extremes[:3] if r >= 20],
        "reads": {"calls": tools["Read"], "rereads": reread_total,
                  "most_reread": [{"file": _short(f), "times": n} for f, n in rereads.most_common(3)]},
        "long_sessions": {"count": len(long),
                          "without_checkpoint": sum(s["checkpoints"] == 0 for s in long),
                          "without_save": sum(s["saves"] == 0 for s in long)},
        "subagents": {"sessions_using": sum(s["agents"] > 0 for s in sessions),
                      "total": sum(s["agents"] for s in sessions),
                      "max_in_one_session": max((s["agents"] for s in sessions), default=0)},
        "projects": dict(sorted(per_project.items(), key=lambda kv: -kv[1]["turns"])[:8]),
    }


def _short(path: str) -> str:
    home = str(Path.home())
    return path.replace(home, "~") if path.startswith(home) else path


def _tool_name(tool: str) -> str:
    """mcp__memory__memory_save -> memory_save: the server prefix is noise in a digest."""
    return tool.split("__", 2)[-1] if tool.startswith("mcp__") else tool


def format_digest(agg: dict, since: str | None = None) -> str:
    if not agg["sessions"]:
        return f"No sessions found (need ≥{MIN_TURNS} assistant turns). Set MEMORY_BOOST_TRANSCRIPTS?\n"
    b, r, ls, sa = agg["bash"], agg["reads"], agg["long_sessions"], agg["subagents"]
    pct = 100 * b["failed"] / max(b["calls"], 1)
    lines = [f"# Session digest{' since ' + since if since else ''} — {_today()}",
             f"sessions      {agg['sessions']} with ≥{MIN_TURNS} turns",
             "tools         " + " · ".join(f"{_tool_name(k)} {v}" for k, v in list(agg["tools"].items())[:6]),
             f"bash          {b['calls']} calls, {b['failed']} failed ({pct:.1f}%)"
             + ("; most failed: " + ", ".join(f"{k} ({v})" for k, v in b["top_failed_cmds"].items())
                if b["top_failed_cmds"] else ""),
             f"bash/edit     {agg['bash_per_edit']}x"
             + ("; extreme: " + ", ".join(f"{e['project']} {e['ratio']}x" for e in agg["extreme_sessions"])
                if agg["extreme_sessions"] else ""),
             f"reads         {r['calls']} calls, {r['rereads']} re-reads of a file already read "
             f"({100 * r['rereads'] / max(r['calls'], 1):.0f}%)"
             + ("; top: " + ", ".join(f"{m['file']} ×{m['times']}" for m in r["most_reread"])
                if r["most_reread"] else ""),
             f"discipline    {plural(ls['count'], 'long session')} (≥{LONG_SESSION} turns): "
             f"{ls['without_checkpoint']} never checkpointed, {ls['without_save']} never saved",
             f"subagents     {sa['total']} spawned in {plural(sa['sessions_using'], 'session')}; "
             f"max in one session: {sa['max_in_one_session']}",
             "projects      " + ", ".join(f"{p} ({v['sessions']}s/{v['turns']}t)"
                                          for p, v in agg["projects"].items()),
             "next: memory-boost lesson --help   # turn one line above into a lesson"]
    return "\n".join(lines) + "\n"
