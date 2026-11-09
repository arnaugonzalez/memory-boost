"""Automatic checkpoints from the harness transcript, with no LLM.

Stop and PreCompact hooks call `autosave()`. It reads only the transcript bytes
written since the last autosave (offset kept in the checkpoint itself), pulls
out the facts a transcript states without interpretation — user prompts, files
edited, git commits — and merges them under the checkpoint's `auto` key. What
the agent wrote (task, done, next, notes) is never overwritten.

Stop is throttled: it writes only when something changed AND either
MEMORY_BOOST_AUTOSAVE_SECS (default 600) passed or MEMORY_BOOST_AUTOSAVE_TURNS
(default 4; 0 turns this trigger off) prompts accumulated. PreCompact always writes: it is the last
chance before the context is summarised away. Stdlib only, like the hook path.

With MEMORY_BOOST_AUTOSAVE_LOG=1 each written checkpoint also upserts one
`auto` entry per session in wiki/log.md (replaced in place on every write, so a
session never adds more than one entry). With MEMORY_BOOST_AUTOSAVE_PUSH set to
a shell command, that command then runs detached (the hook must not wait on the
network); its output goes to autosave-push.log in the checkpoints directory."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from memory_boost import core

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PRIVATE = re.compile(r"<private>.*?</private>", re.S | re.I)
NOISE_PREFIXES = ("<command-", "<local-command", "<system-reminder", "<task-notification",
                  "Caveat:", "[Request interrupted",
                  "This session is being continued from a previous conversation")
COMMIT_MSG = re.compile(r"""git\s+commit\b.*?-m\s+(?:"([^"]+)"|'([^']+)'|\$\(cat\s+<<'?EOF'?\s*\n([^\n]+))""",
                        re.S)
PROMPT_CAP = 160
RECENT_PROMPTS = 4
QUICK_PROMPTS = 4  # a session with fewer prompts and no edits is a quick question
LIST_CAP = 30


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _prompt_text(content) -> str | None:
    """The user's own words in a transcript `user` record, or None for tool
    results, slash-command plumbing and harness reminders."""
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        if not texts or any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = "\n".join(texts)
    if not isinstance(content, str):
        return None
    text = PRIVATE.sub("", content).strip()
    if not text or text.startswith(NOISE_PREFIXES):
        return None
    text = " ".join(text.split())
    return text if len(text) <= PROMPT_CAP else text[:PROMPT_CAP - 1] + "…"


def _rel(path: str, cwd: str) -> str:
    try:
        return str(Path(path).relative_to(cwd))
    except ValueError:
        return path


def read_delta(transcript: Path, offset: int, cwd: str) -> tuple[dict, int]:
    """Facts from `transcript` after byte `offset`. Returns (facts, new_offset).
    A partial last line (being written right now) is left for the next call."""
    facts = {"prompts": [], "files": [], "commits": []}
    size = transcript.stat().st_size
    if offset > size:  # file was replaced: start over
        offset = 0
    with transcript.open("rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    for raw in data[:end].splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") == "user" and not rec.get("isMeta") and not rec.get("isSidechain"):
            text = _prompt_text(msg.get("content"))
            if text and text not in facts["prompts"][-1:]:  # the harness may log a prompt twice
                facts["prompts"].append(text)
        elif rec.get("type") == "assistant" and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                inp = b.get("input") or {}
                if b.get("name") in EDIT_TOOLS:
                    p = inp.get("file_path") or inp.get("notebook_path")
                    if p:
                        facts["files"].append(_rel(p, cwd))
                elif b.get("name") == "Bash":
                    m = COMMIT_MSG.search(inp.get("command", ""))
                    if m:
                        facts["commits"].append(next(g for g in m.groups() if g).strip().splitlines()[0][:PROMPT_CAP])
    return facts, offset + end


def _merge(old: list, new: list, cap: int = LIST_CAP) -> list:
    out = list(old)
    for x in new:
        if x not in out:
            out.append(x)
    return out[-cap:]


def autosave(event: dict, trigger: str) -> dict | None:
    """Write the auto part of this session's checkpoint. `trigger` is "stop" or
    "pre-compact". Returns the checkpoint written, or None when skipped."""
    sid, tpath = event.get("session_id"), event.get("transcript_path")
    cwd = event.get("cwd") or str(Path.cwd())
    project = core.resolve_project(cwd)
    if not (sid and tpath and project) or not Path(tpath).is_file():
        return None
    prev = core.load_checkpoint(project, sid) or {}
    auto = prev.get("auto") or {}
    facts, offset = read_delta(Path(tpath), int(auto.get("offset", 0)), cwd)
    # The offset only advances on a real write, so a skipped Stop loses nothing:
    # the next one re-reads the whole unsaved stretch, prompts included.
    changed = any(facts.values())
    prompts = facts["prompts"]
    now = dt.datetime.now(dt.UTC)
    turns_due = _env_int("MEMORY_BOOST_AUTOSAVE_TURNS", 4)
    if trigger == "stop":
        if not changed:
            return None
        if not prev and not (facts["files"] or facts["commits"]) and len(prompts) < QUICK_PROMPTS:
            return None  # a quick question: no checkpoint until the session does real work
        last = auto.get("at")
        elapsed = (now - dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
                   ).total_seconds() if last else float("inf")
        if elapsed < _env_int("MEMORY_BOOST_AUTOSAVE_SECS", 600) and not (
                0 < turns_due <= len(prompts)):
            return None
    elif prev and not changed:
        return None

    auto = {
        "trigger": trigger,
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "offset": offset,
        "turns": int(auto.get("turns", 0)) + len(prompts),
        "request": auto.get("request") or (prompts[0] if prompts else ""),
        "recent_prompts": _merge(auto.get("recent_prompts", []), prompts)[-RECENT_PROMPTS:],
        "files_edited": _merge(auto.get("files_edited", []), facts["files"]),
        "commits": _merge(auto.get("commits", []), facts["commits"]),
    }
    if prev:
        ckpt = dict(prev, auto=auto, updated=auto["at"],
                    files=sorted(set(prev.get("files", [])) | set(facts["files"])))
    else:
        ckpt = {
            "project": core.norm_project(project), "session_id": sid, "agent": "auto-hook",
            "created": auto["at"], "updated": auto["at"], "status": "active",
            "task": auto["request"] or "(auto checkpoint)", "done": [], "next": "",
            "files": sorted(set(facts["files"])), "notes": [], "plan_ref": None, "auto": auto,
        }
    core.write_checkpoint(project, sid, ckpt)
    if os.environ.get("MEMORY_BOOST_AUTOSAVE_LOG") == "1":
        core.upsert_session_log(ckpt["project"], sid, _log_bullets(ckpt))
        _push()
    return ckpt


def _log_bullets(ckpt: dict) -> list[str]:
    auto = ckpt["auto"]
    bullets = [f"Task: {ckpt['task']}"] if ckpt.get("task") else []
    if ckpt.get("done"):
        bullets.append("Done: " + "; ".join(ckpt["done"][-5:]))
    if ckpt.get("next"):
        bullets.append(f"Next: {ckpt['next']}")
    if auto.get("recent_prompts"):
        bullets.append("Last prompts: " + " / ".join(auto["recent_prompts"]))
    if auto.get("files_edited"):
        files = auto["files_edited"]
        bullets.append(f"Files ({len(files)}): " + ", ".join(Path(f).name for f in files[-10:]))
    if auto.get("commits"):
        bullets.append("Commits: " + "; ".join(auto["commits"][-5:]))
    bullets.append(f"Turns: {auto.get('turns', 0)} · last autosave {auto['at']} ({auto['trigger']})")
    return bullets


def _push() -> None:
    cmd = os.environ.get("MEMORY_BOOST_AUTOSAVE_PUSH")
    if not cmd:
        return
    core.checkpoints_dir().mkdir(parents=True, exist_ok=True)
    with open(core.checkpoints_dir() / "autosave-push.log", "ab") as out:
        subprocess.Popen(cmd, shell=True, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True)
