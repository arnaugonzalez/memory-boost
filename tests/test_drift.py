import json

from memory_boost import core, drift

TODAY = "2026-09-24"


def kinds(report):
    return sorted(f["kind"] for f in report["findings"])


def test_example_wiki_shows_each_kind_once(home):
    """The bundled example is the demo: one finding of each wiki-level kind, most urgent first."""
    r = drift.drift_report(today=TODAY)
    assert kinds(r) == ["decision-expired", "decision-no-review", "decision-superseded-orphan",
                        "project-no-decisions", "project-silent"]
    f = r["findings"][0]
    assert f["title"] == "pin-postgres-15" and f["project"] == "acme-api" and f["severity"] == 1
    by_kind = {f["kind"]: f["title"] for f in r["findings"]}
    assert by_kind["decision-superseded-orphan"] == "sessions-in-redis"
    assert by_kind["decision-no-review"] == "cors-allow-all"
    assert by_kind["project-no-decisions"] == "pixel-notes"
    assert by_kind["project-silent"] == "legacy-dashboard"
    assert r["totals"] == {"decisions": 5, "projects": 3, "log_entries": 12, "open_checkpoints": 0,
                           "findings": 5}


def test_report_is_deterministic(home):
    a, b = drift.drift_report(today=TODAY), drift.drift_report(today=TODAY)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_expired_decision_disappears_once_marked_historic(home):
    p = core.wiki_dir() / "decisions" / "pin-postgres-15.md"
    p.write_text(p.read_text().replace("review_by: 2026-06-01", "status: historic\nreview_by: 2026-06-01"))
    assert "decision-expired" not in kinds(drift.drift_report(today=TODAY))


def test_superseded_without_successor_is_an_orphan(home):
    p = core.wiki_dir() / "decisions" / "float-money.md"
    p.write_text(p.read_text().replace("superseded_by: integer-cents\n", ""))
    assert "decision-superseded-orphan" in kinds(drift.drift_report(today=TODAY))


def test_missing_review_by_is_low_severity(home):
    p = core.wiki_dir() / "decisions" / "queue-in-postgres.md"
    p.write_text(p.read_text().replace("review_by: 2027-03-01\n", ""))
    hit = [f for f in drift.drift_report(today=TODAY)["findings"]
           if f["kind"] == "decision-no-review" and f["title"] == "queue-in-postgres"]
    assert hit and hit[0]["severity"] == 3


def test_silent_and_never_logged_projects(home):
    (core.wiki_dir() / "projects" / "ghost.md").write_text("---\ntitle: ghost\n---\n# ghost\n")
    r = drift.drift_report(today="2026-12-01")  # 3 months after the last log entry
    silent = {f["title"]: f for f in r["findings"] if f["kind"] == "project-silent"}
    assert silent["ghost"]["severity"] == 3 and "never logged" in silent["ghost"]["detail"]
    assert silent["acme-api"]["severity"] == 2 and "2026-09-01" in silent["acme-api"]["detail"]
    assert "pixel-notes" in silent


def test_busy_project_without_decisions(home):
    for i in range(drift.BUSY_SESSIONS):
        core.append_log("pixel-notes", "codex", f"session {i}")
    r = drift.drift_report(today=TODAY)
    hit = [f for f in r["findings"] if f["kind"] == "project-no-decisions"]
    assert [f["project"] for f in hit] == ["pixel-notes"]
    # acme-api is tagged on decisions, so it never shows up here even if busy
    for i in range(drift.BUSY_SESSIONS):
        core.append_log("acme-api", "codex", f"session {i}")
    assert [f["project"] for f in drift.drift_report(today=TODAY)["findings"]
            if f["kind"] == "project-no-decisions"] == ["pixel-notes"]


def test_open_checkpoint_older_than_a_week(home):
    c = core.save_checkpoint("acme-api", "sess-1", "load test", agent="t")
    assert "checkpoint-open" not in kinds(drift.drift_report(today=c["updated"][:10]))
    late = drift.drift_report(today="2026-12-01")
    open_ = [f for f in late["findings"] if f["kind"] == "checkpoint-open"]
    assert len(open_) == 1 and open_[0]["project"] == "acme-api" and "load test" in open_[0]["detail"]
    core.save_checkpoint("acme-api", "sess-1", "load test", status="done")
    later = drift.drift_report(today="2026-12-01")["findings"]
    assert not [f for f in later if f["kind"] == "checkpoint-open"]


def test_for_project_and_format(home):
    lines = drift.for_project("ACME_API", today=TODAY)
    assert len(lines) == 3 and lines[0].startswith("- decision-expired: pin-postgres-15")
    pixel = drift.for_project("pixel-notes", today=TODAY)
    assert [x.split(":")[0] for x in pixel] == ["- project-no-decisions"]
    text = drift.format_report(drift.drift_report(today=TODAY))
    assert text.startswith("# Drift report — 2026-09-24") and "## decision-expired (1)" in text


def test_empty_wiki_reports_nothing(home, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    for sub in ("projects", "decisions", "concepts"):
        (empty / sub).mkdir(parents=True)
    monkeypatch.setenv("MEMORY_BOOST_WIKI", str(empty))
    r = drift.drift_report(today=TODAY)
    assert r["findings"] == [] and "Nothing drifted" in drift.format_report(r)


def test_malformed_review_by_is_reported_not_raised(home):
    (core.wiki_dir() / "decisions" / "weird.md").write_text(
        "---\ntitle: weird\nreview_by: not-a-date\n---\n# w\n")
    bad = [f for f in drift.drift_report(today=TODAY)["findings"] if f["kind"] == "decision-bad-date"]
    assert len(bad) == 1 and bad[0]["title"] == "weird"
