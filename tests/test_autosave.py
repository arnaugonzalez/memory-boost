import json
import os
import subprocess
import sys

from memory_boost import autosave, cli, core


def _rec(kind, content, **extra):
    role = "user" if kind == "user" else "assistant"
    return {"type": kind, "message": {"role": role, "content": content}, **extra}


def _tool(name, **inp):
    return _rec("assistant", [{"type": "tool_use", "name": name, "input": inp}])


def _write(path, recs, partial=""):
    with path.open("a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        f.write(partial)


def _event(tmp_path, t):
    return {"session_id": "s1", "transcript_path": str(t), "cwd": str(tmp_path / "acme-api")}


def test_extracts_prompts_files_commits_and_skips_noise(home, tmp_path):
    t = tmp_path / "t.jsonl"
    cwd = tmp_path / "acme-api"
    _write(t, [
        _rec("user", "add retries <private>token=abc</private> to the worker"),
        _rec("user", [{"type": "tool_result", "content": "ok"}]),
        _rec("user", "<command-name>/clear</command-name>"),
        _rec("user", "meta stuff", isMeta=True),
        _tool("Edit", file_path=str(cwd / "src/worker.py")),
        _tool("Bash", command='git add -A && git commit -m "feat: retries"'),
        _tool("Read", file_path="/etc/hosts"),
    ], partial='{"type": "user", "mess')
    facts, offset = autosave.read_delta(t, 0, str(cwd))
    assert facts == {"prompts": ["add retries to the worker"], "files": ["src/worker.py"],
                     "commits": ["feat: retries"]}
    assert offset < t.stat().st_size  # the half-written line is left for next time


def test_stop_is_throttled_and_loses_nothing(home, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_BOOST_AUTOSAVE_TURNS", "3")
    t = tmp_path / "t.jsonl"
    ev = _event(tmp_path, t)
    _write(t, [_rec("user", "first ask")])
    assert autosave.autosave(ev, "stop") is None  # one quick question: no checkpoint yet
    _write(t, [_tool("Edit", file_path=str(tmp_path / "acme-api/a.py"))])
    assert autosave.autosave(ev, "stop")["auto"]["request"] == "first ask"  # real work: due
    _write(t, [_rec("user", "two"), _rec("user", "three")])
    assert autosave.autosave(ev, "stop") is None  # 2 prompts, <10 min: skipped
    _write(t, [_rec("user", "four")])
    ck = autosave.autosave(ev, "stop")  # 3 unsaved prompts re-read from the old offset
    assert ck["auto"]["turns"] == 4 and ck["auto"]["recent_prompts"][-3:] == ["two", "three", "four"]
    assert autosave.autosave(ev, "stop") is None  # nothing new


def test_pre_compact_always_writes_and_keeps_agent_fields(home, tmp_path):
    t = tmp_path / "t.jsonl"
    ev = _event(tmp_path, t)
    core.save_checkpoint("acme-api", "s1", "ship v2", next_step="deploy", done=["tests"])
    _write(t, [_rec("user", "go")])
    ck = autosave.autosave(ev, "pre-compact")
    assert (ck["task"], ck["next"], ck["done"]) == ("ship v2", "deploy", ["tests"])
    assert ck["auto"]["trigger"] == "pre-compact"
    # the agent's next checkpoint keeps the auto block
    assert core.save_checkpoint("acme-api", "s1", "ship v2", done=["deploy"])["auto"]["turns"] == 1
    text = cli.session_start_payload({"cwd": "/w/acme-api", "session_id": "s1", "source": "compact"})
    assert "Auto-captured (pre-compact" in text and "recent prompt: go" in text


def test_unknown_project_or_missing_transcript_is_a_noop(home, tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_rec("user", "x")])
    assert autosave.autosave({**_event(tmp_path, t), "cwd": "/w/nope"}, "stop") is None
    assert autosave.autosave({**_event(tmp_path, t), "transcript_path": "/no/file"}, "stop") is None


def test_hook_stop_prints_nothing_and_never_fails(home, tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_rec("user", "x")])
    for ev in ["", "garbage", json.dumps(_event(tmp_path, t))]:
        for name in ("stop", "pre-compact"):
            r = subprocess.run([sys.executable, "-m", "memory_boost.cli", "hook", name], input=ev,
                               capture_output=True, text=True, env=dict(os.environ))
            assert (r.returncode, r.stdout) == (0, "")


def test_truncate_cuts_a_long_line_instead_of_dropping_it():
    text = "Projects: " + ", ".join(f"proj{i}" for i in range(200))
    out = core.truncate(text, 300, "memory_page")
    assert out.startswith("Projects: proj0, proj1") and out.endswith("(truncated, see memory_page)")


def test_drift_ignores_auto_only_checkpoints(home, tmp_path):
    from memory_boost import drift
    t = tmp_path / "t.jsonl"
    _write(t, [_rec("user", "go")])
    autosave.autosave(_event(tmp_path, t), "pre-compact")
    assert drift.drift_report()["totals"]["open_checkpoints"] == 0
