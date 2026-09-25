import json
import shutil
from importlib import resources
from pathlib import Path

import pytest

from memory_boost import mine


@pytest.fixture
def transcripts(tmp_path):
    src = resources.files("memory_boost") / "example_transcripts"
    with resources.as_file(src) as p:
        shutil.copytree(p, tmp_path / "t")
    return tmp_path / "t"


def test_scan_keeps_only_work_sessions(transcripts):
    sessions = mine.scan([transcripts])
    assert sorted(s["file"][:12] for s in sessions) == ["0a1b2c3d-111", "0a1b2c3d-222", "0a1b2c3d-333"]
    assert {s["project"] for s in sessions} == {"acme-api", "pixel-notes"}
    assert mine.scan([transcripts], min_turns=1) and len(mine.scan([transcripts], min_turns=1)) == 4


def test_since_filters_on_start_date(transcripts):
    assert len(mine.scan([transcripts], since="2026-09-10")) == 2
    assert mine.scan([transcripts], since="2027-01-01") == []


def test_aggregate_matches_the_generator(transcripts):
    agg = mine.aggregate(mine.scan([transcripts]))
    assert agg["sessions"] == 3
    assert agg["tools"]["Bash"] == 76 and agg["tools"]["Edit"] == 23 and agg["tools"]["Agent"] == 5
    assert agg["bash"] == {"calls": 76, "failed": 13, "top_failed_cmds": {"cd": 12, "sleep": 1}}
    assert agg["reads"]["rereads"] == 6
    assert agg["reads"]["most_reread"][0] == {"file": "/work/acme-api/src/queue.py", "times": 6}
    assert agg["long_sessions"] == {"count": 1, "without_checkpoint": 1, "without_save": 1}
    assert agg["subagents"] == {"sessions_using": 1, "total": 5, "max_in_one_session": 5}
    assert list(agg["projects"]) == ["acme-api", "pixel-notes"]


def test_digest_never_leaks_contents(transcripts):
    text = mine.format_digest(mine.aggregate(mine.scan([transcripts])))
    assert "(synthetic" not in text and "old_string" not in text and "pytest -q" not in text
    assert "sessions      3" in text and "cd (12)" in text


def test_deterministic(transcripts):
    a = json.dumps(mine.aggregate(mine.scan([transcripts])), sort_keys=True)
    b = json.dumps(mine.aggregate(mine.scan([transcripts])), sort_keys=True)
    assert a == b


def test_broken_lines_and_missing_dirs(tmp_path):
    d = tmp_path / "x" / "-work-p"
    d.mkdir(parents=True)
    good = json.dumps({"type": "assistant", "cwd": "/work/p", "timestamp": "2026-09-01T00:00:00Z",
                       "message": {"content": [{"type": "tool_use", "id": "1", "name": "Bash",
                                                "input": {"command": "ls"}}]}})
    (d / "s.jsonl").write_text("not json\n" + "\n".join([good] * 25) + "\n[1,2]\n" + json.dumps("str") + "\n")
    sessions = mine.scan([tmp_path / "x", tmp_path / "does-not-exist"])
    assert len(sessions) == 1 and sessions[0]["turns"] == 25 and sessions[0]["project"] == "p"


def test_empty_digest_message():
    assert "No sessions found" in mine.format_digest(mine.aggregate([]))


def test_roots_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_BOOST_TRANSCRIPTS", f"{tmp_path / 'a'}:{tmp_path / 'b'}")
    assert mine.transcript_roots() == [tmp_path / "a", tmp_path / "b"]
    monkeypatch.delenv("MEMORY_BOOST_TRANSCRIPTS")
    assert mine.transcript_roots() == [Path.home() / ".claude" / "projects"]


def test_digest_hides_home_and_shortens_mcp_names(transcripts, monkeypatch):
    monkeypatch.setenv("HOME", "/work")  # the example transcripts live under /work/...
    text = mine.format_digest(mine.aggregate(mine.scan([transcripts])))
    assert "/work/" not in text and "~/acme-api/src/queue.py ×6" in text
    assert "mcp__" not in text and "memory_checkpoint 3" in text
