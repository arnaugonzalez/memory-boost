import json

from memory_boost import cli, core, lessons, reflect


def _write_events(home, agent: str, events: list[dict]) -> None:
    p = core.events_dir() / agent / "2026-09-24.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def test_reflect_filters_single_project_vs_cross_project(home):
    _write_events(home, "claude", [
        {
            "ts": "2026-09-24T10:00:00Z",
            "date_local": "2026-09-24",
            "project": "acme-api",
            "session": {"id": "s1"},
            "friction": [{"what": "x1", "kind": "config", "cause": "missing-env-check"}],
        },
        {
            "ts": "2026-09-24T11:00:00Z",
            "date_local": "2026-09-24",
            "project": "acme-api",
            "session": {"id": "s2"},
            "friction": [{"what": "x2", "kind": "config", "cause": "missing-env-check"}],
        },
        {
            "ts": "2026-09-24T12:00:00Z",
            "date_local": "2026-09-24",
            "project": "acme-api",
            "session": {"id": "s3"},
            "friction": [{"what": "y1", "kind": "tool", "cause": "stale-lock-file", "fix": "Clean lock before run"}],
        },
        {
            "ts": "2026-09-24T13:00:00Z",
            "date_local": "2026-09-24",
            "project": "pixel-notes",
            "session": {"id": "s4"},
            "friction": [{"what": "y2", "kind": "tool", "cause": "stale-lock-file", "fix": "Clean lock before run"}],
        },
    ])

    res = reflect.reflect_candidates(min_projects=2, min_sessions=2)
    names = [c["name"] for c in res["candidates"]]
    assert "stale-lock-file" in names
    assert "missing-env-check" not in names  # 1 project and no fix
    cand = next(c for c in res["candidates"] if c["name"] == "stale-lock-file")
    assert cand["title"] == "Clean lock before run"
    assert "last 2026-09-24" in cand["evidence"]


def test_reflect_deduplicates_existing_lesson(home):
    lessons.save_lesson(
        "stale-lock-file",
        "Clean lock before run",
        ["python"],
        "Already documented.\n",
        learned_on="acme-api",
        evidence="seen before",
        confidence="high",
    )
    _write_events(home, "claude", [
        {
            "ts": "2026-09-24T10:00:00Z",
            "date_local": "2026-09-24",
            "project": "acme-api",
            "session": {"id": "s1"},
            "friction": [{"what": "y1", "kind": "tool", "cause": "stale-lock-file", "fix": "Clean lock before run"}],
        },
        {
            "ts": "2026-09-24T11:00:00Z",
            "date_local": "2026-09-24",
            "project": "pixel-notes",
            "session": {"id": "s2"},
            "friction": [{"what": "y2", "kind": "tool", "cause": "stale-lock-file", "fix": "Clean lock before run"}],
        },
    ])
    res = reflect.reflect_candidates()
    assert res["candidates"] == []
    assert any(s["lesson"] == "stale-lock-file" for s in res["skipped_existing"])


def test_reflect_cli_json_and_apply(home, capsys):
    _write_events(home, "claude", [
        {
            "ts": "2026-09-24T10:00:00Z",
            "date_local": "2026-09-24",
            "project": "acme-api",
            "session": {"id": "s1"},
            "friction": [{"what": "z1", "kind": "test", "cause": "verify-schema-early", "fix": "Validate schema before deploy"}],
        },
        {
            "ts": "2026-09-24T11:00:00Z",
            "date_local": "2026-09-24",
            "project": "pixel-notes",
            "session": {"id": "s2"},
            "friction": [{"what": "z2", "kind": "test", "cause": "verify-schema-early", "fix": "Validate schema before deploy"}],
        },
    ])

    cli.main(["reflect", "--json"])
    dry = json.loads(capsys.readouterr().out)
    assert dry["version"] == cli.JSON_VERSION
    assert len(dry["candidates"]) == 1
    assert dry["applied"] == []
    assert not (core.wiki_dir() / "concepts" / "verify-schema-early.md").exists()

    cli.main(["reflect", "--json", "--apply"])
    applied = json.loads(capsys.readouterr().out)
    assert len(applied["applied"]) == 1
    assert (core.wiki_dir() / "concepts" / "verify-schema-early.md").exists()
