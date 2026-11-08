"""Lessons with evidence, setup friction grouped by cause, and the drift checks around them."""
import json
import os
import subprocess
import sys

import pytest

from memory_boost import core, drift, friction, lessons

TODAY = "2026-11-07"


def run(args):
    return subprocess.run([sys.executable, "-m", "memory_boost.cli", *args],
                          capture_output=True, text=True, env=dict(os.environ))


def test_lesson_evidence_round_trips_and_shows_trust(home):
    lessons.save_lesson("pin-lockfile", "Pin the lockfile in CI", ["ci", "python"], "body",
                        learned_on="pixel-notes", cue="CI installs  deps\nfrom ranges",
                        evidence="3 red builds before, 0 in 20 after", confidence="medium")
    got = {lesson["name"]: lesson for lesson in lessons.all_lessons()}["pin-lockfile"]
    assert got["cue"] == "CI installs deps from ranges"           # collapsed to one line
    assert (got["evidence"], got["confidence"]) == ("3 red builds before, 0 in 20 after", "medium")
    line = lessons.format_lessons([{**got, "matched": ["ci"]}])[0]
    assert "confidence medium" in line
    old = {lesson["name"]: lesson for lesson in lessons.all_lessons()}["money-as-integer-cents"]
    assert "unproven" in lessons.format_lessons([{**old, "matched": ["payments"]}])[0]


@pytest.mark.parametrize("kwargs, msg", [
    ({"confidence": "sure", "evidence": "x"}, "invalid confidence"),
    ({"confidence": "high"}, "confidence without evidence"),
    ({"confidence": "high", "evidence": "   "}, "confidence without evidence"),
])
def test_lesson_rejects_bad_confidence(home, kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        lessons.save_lesson("x", "t", ["ci"], "body", **kwargs)
    assert not (core.wiki_dir() / "concepts" / "x.md").exists()


def test_drift_flags_projects_no_lesson_can_reach(home):
    p = core.wiki_dir() / "projects" / "acme-api.md"
    p.write_text("\n".join(ln for ln in p.read_text().splitlines() if not ln.startswith("tags:")) + "\n")
    hits = [f for f in drift.drift_report(today=TODAY)["findings"] if f["kind"] == "project-no-tags"]
    assert [f["project"] for f in hits] == ["acme-api"] and "2 lessons" in hits[0]["detail"]


def test_no_tags_is_not_drift_when_there_are_no_lessons(home):
    for p in (core.wiki_dir() / "concepts").glob("*.md"):
        p.unlink()
    p = core.wiki_dir() / "projects" / "acme-api.md"
    p.write_text(p.read_text().replace("tags:", "nottags:"))
    assert "project-no-tags" not in {f["kind"] for f in drift.drift_report(today=TODAY)["findings"]}


def _save(project, sid, items):
    core.save_full(project, "work", session={"id": sid, "harness": "claude-code"}, friction=items)


def test_friction_needs_two_sessions_to_become_a_proposal(home):
    env_miss = {"what": "had to guess the DSN", "kind": "guess", "cause": "Missing Env Var",
                "fix": "document DSN in AGENTS.md"}
    _save("acme-api", "s1", [env_miss, {"what": "flaky port", "kind": "time", "cause": "port-clash"}])
    _save("pixel-notes", "s2", [{**env_miss, "what": "DSN not set", "fix": None}])
    r = friction.report()
    assert r["signals"] == 3 and r["causes"] == 2
    [p] = r["proposals"]
    assert p["cause"] == "missing-env-var" and p["sessions"] == 2
    assert p["projects"] == ["acme-api", "pixel-notes"] and p["kinds"] == {"guess": 2}
    assert p["fixes"] == ["document DSN in AGENTS.md"]
    assert r["noise"] == ["port-clash"]
    assert friction.report(project="pixel-notes")["proposals"] == []   # 1 session inside the scope


def test_friction_same_session_twice_is_still_one_session(home):
    item = {"what": "w", "kind": "workaround", "cause": "c"}
    _save("acme-api", "same", [item])
    _save("acme-api", "same", [item])
    assert friction.report()["proposals"] == []


def test_friction_empty_report_says_keep_the_setup(home):
    text = friction.format_report(friction.report())
    assert "0 signals" in text and "valid outcome" in text


@pytest.mark.parametrize("bad, msg", [
    ([{"what": "w", "kind": "boring", "cause": "c"}], "invalid friction.kind"),
    ([{"what": "w", "kind": "time"}], "friction\\[\\].cause"),
    ([{"what": "w", "kind": "time", "cause": "c", "fix": 3}], "fix must be a string"),
    ("not a list", "list of objects"),
])
def test_friction_is_validated_before_any_write(home, bad, msg):
    with pytest.raises(ValueError, match=msg):
        core.save_full("acme-api", "work", friction=bad)
    assert not any(e.get("action") == "work" for e in core.iter_events())


def test_friction_cli_and_since_window(home):
    _save("acme-api", "s1", [{"what": "w", "kind": "time", "cause": "slow-ci"}])
    _save("acme-api", "s2", [{"what": "w2", "kind": "time", "cause": "slow-ci"}])
    out = json.loads(run(["friction", "--json"]).stdout)
    assert out["proposals"][0]["cause"] == "slow-ci"
    assert "## 1. slow-ci" in run(["friction"]).stdout
    bad = run(["friction", "--since", "week"])
    assert bad.returncode != 0 and "7d" in bad.stderr
    r = run(["save", "--project", "acme-api", "--action", "a", "--fields-json",
             json.dumps({"friction": [{"what": "w", "kind": "guess", "cause": "x"}]})])
    assert r.returncode == 0, r.stderr
