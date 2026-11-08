import json

import pytest

from memory_boost import core


def test_date_local_follows_memory_boost_tz(home, monkeypatch):
    ts = "2026-10-28T23:30:00Z"  # after midnight in Madrid (CET, +1), still the 28th in UTC
    monkeypatch.setenv("MEMORY_BOOST_TZ", "Europe/Madrid")
    assert core.date_local(ts) == "2026-10-29"
    monkeypatch.setenv("MEMORY_BOOST_TZ", "UTC")
    assert core.date_local(ts) == "2026-10-28"
    monkeypatch.delenv("MEMORY_BOOST_TZ")
    assert core.date_local(ts) == "2026-10-28"


def test_parse_pending_string_is_split_on_semicolons():
    assert core.parse_pending("a; b ;") == [
        {"text": "a", "owner": "user", "severity": 3},
        {"text": "b", "owner": "user", "severity": 3},
    ]
    assert core.parse_pending("") == []
    assert core.parse_pending(None) == []


def test_parse_pending_list_keeps_optional_fields():
    items = core.parse_pending([{"text": "Respuesta al presupuesto", "owner": "client",
                                 "severity": 2, "due": "2026-11-05", "waiting_on": "org:acme-corp"}])
    assert items == [{"text": "Respuesta al presupuesto", "owner": "client", "severity": 2,
                      "due": "2026-11-05", "waiting_on": "org:acme-corp"}]


@pytest.mark.parametrize("bad", [
    [{"text": "x", "owner": "robot"}],
    [{"text": "x", "severity": 4}],
    [{"text": "x", "due": "mañana"}],
    [{"text": "  "}],
    42,
])
def test_parse_pending_rejects_invalid_input(bad):
    with pytest.raises(ValueError):
        core.parse_pending(bad)


def test_save_full_assigns_pending_ids_and_writes_v2_event(home):
    out = core.save_full("acme-api", "two things",
                         pending=[{"text": "deploy", "severity": 1}, {"text": "docs"}])
    assert out["pending_ids"] == ["acme-api#1", "acme-api#2"]
    ev = core.iter_events()[-1]
    assert ev["schema"] == core.SCHEMA_V2
    assert [p["id"] for p in ev["pending"]] == ["acme-api#1", "acme-api#2"]
    assert ev["pending"][0]["severity"] == 1
    assert ev["seq"] == 1
    assert ev["date_local"] == core.date_local(ev["ts"])


def test_closes_removes_pending_from_open_fold(home):
    core.save_full("acme-api", "first", pending="a; b")
    core.save_full("acme-api", "second", closes=["acme-api#1"])
    assert [p["id"] for p in core.open_pending("acme-api")] == ["acme-api#2"]


def test_drops_also_closes_pending(home):
    core.save_full("acme-api", "first", pending="a; b")
    core.save_full("acme-api", "second", drops=[{"id": "acme-api#1", "why": "ya no aplica"}])
    assert [p["id"] for p in core.open_pending()] == ["acme-api#2"]


def test_open_pending_marks_old_items_stale(home):
    # 2026-08-01 is 54 days before the pinned MEMORY_BOOST_TODAY (2026-09-24)
    d = core.events_dir() / "mcp"
    d.mkdir(parents=True)
    line = {"schema": core.SCHEMA_V2, "ts": "2026-08-01T10:00:00Z", "agent": "mcp",
            "project": "acme-api", "action": "old", "result": "ok",
            "pending": [{"id": "acme-api#9", "text": "vieja", "owner": "user", "severity": 3}]}
    (d / "2026-08-01.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    (open_item,) = core.open_pending("acme-api")
    assert open_item["id"] == "acme-api#9"
    assert open_item["stale"] is True
    assert (core.events_dir() / "pending-open.json").exists()


def test_session_seq_and_segment_start(home):
    first = core.save_full("acme-api", "a", session={"id": "sess-1"})
    ts1 = first["event"]["ts"]
    assert first["event"]["seq"] == 1
    assert first["event"]["session"]["segment_start"] == ts1
    assert core.session_seq("sess-1") == (2, ts1)
    second = core.save_full("acme-api", "b", session={"id": "sess-1"})
    assert second["event"]["seq"] == 2
    assert second["event"]["session"]["segment_start"] == ts1


def test_log_entry_has_pending_ids_and_stays_parseable(home):
    core.save_full("acme-api", "migrated the thing",
                   pending=[{"text": "Lanzar batch", "severity": 3}])
    text = (home / "wiki/log.md").read_text(encoding="utf-8")
    assert "[acme-api#1|s3] Lanzar batch" in text
    headers = [line for line in text.splitlines() if line.startswith("## [")]
    assert headers and all(core.LOG_ENTRY_RE.match(h) for h in headers)


def test_log_meta_line_from_session_and_metrics(home):
    core.save_full(
        "acme-api", "x",
        session={"id": "fe02bb1b-0000-4000-8000-000000000000",
                 "segment_start": "2026-10-28T10:00:03Z", "segment_end": "2026-10-28T14:45:12Z"},
        metrics={"minutes": {"acme-api": {"attention": 182, "agent": 160, "gross": 230}},
                 "commits": [{"repo": "acme-api", "sha": "f2c1114", "pushed": False}],
                 "tests": {"runs": 6, "passed": 1180, "failed": 0}, "cost_usd": 14.2},
        outcome="progress")
    text = (home / "wiki/log.md").read_text(encoding="utf-8")
    assert ("- Meta: sid=fe02bb1b seg=10:00–14:45Z min=acme-api 182/230 commits=1(1 unpushed) "
            "tests=1180ok/0fail cost=14.2$ outcome=progress") in text


def test_v1_save_still_returns_slug_and_writes_empty_pending(home):
    assert core.save("acme-api", "x") == "acme-api"
    assert core.iter_events()[-1]["pending"] == []


def test_cli_save_fields_json_then_pending_lists_the_item(home, capsys):
    from memory_boost import cli

    cli.main(["save", "--project", "acme-api", "--action", "v2 cli", "--outcome", "progress",
              "--fields-json",
              '{"pending":[{"text":"x","owner":"client","severity":2,"waiting_on":"org:acme"}]}'])
    assert capsys.readouterr().out.strip() == "saved: acme-api | v2 cli | pending: acme-api#1"

    cli.main(["pending", "--project", "acme-api", "--json"])
    (item,) = json.loads(capsys.readouterr().out)["results"]
    assert (item["id"], item["owner"], item["severity"], item["waiting_on"]) == (
        "acme-api#1", "client", 2, "org:acme")


def test_cli_fields_json_accepts_a_file_and_rejects_unknown_keys(home, tmp_path, capsys):
    from memory_boost import cli

    f = tmp_path / "fields.json"
    f.write_text(json.dumps({"touches": ["acme-api"], "billable": False}), encoding="utf-8")
    cli.main(["save", "--project", "acme-api", "--action", "from file", "--fields-json", f"@{f}"])
    assert core.iter_events()[-1]["touches"] == ["acme-api"]
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        cli.main(["save", "--project", "acme-api", "--action", "bad", "--fields-json", '{"robot":1}'])
    assert exc.value.code == 2 and "unknown keys: robot" in capsys.readouterr().err


def test_cli_pending_text_marks_stale_and_exits_1_when_empty(home, capsys):
    from memory_boost import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["pending"])
    assert exc.value.code == 1
    d = core.events_dir() / "mcp"
    d.mkdir(parents=True)
    line = {"schema": core.SCHEMA_V2, "ts": "2026-08-01T10:00:00Z", "agent": "mcp",
            "project": "acme-api", "action": "old", "result": "ok",
            "pending": [{"id": "acme-api#1", "text": "vieja", "owner": "user", "severity": 3}]}
    (d / "2026-08-01.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    cli.main(["pending", "--project", "acme-api"])
    assert capsys.readouterr().out.strip() == "[acme-api#1|s3|2026-08-01] vieja (user) — still open?"


def test_brief_shows_open_pending_with_ids_and_drops_closed(home):
    core.save_full("acme-api", "two things", pending=[{"text": "deploy", "severity": 3},
                                                       {"text": "docs", "severity": 2}])
    text = core.brief("acme-api")["text"]
    assert "### acme-api — open pending" in text
    assert "[acme-api#1|s3]" in text
    assert "Cite these ids in memory_save(closes=[...]) when done." in text

    core.save_full("acme-api", "closing one", closes=["acme-api#1"])
    text = core.brief("acme-api")["text"]
    assert "[acme-api#1|" not in text
    assert "[acme-api#2|" in text


def test_brief_marks_stale_pending_and_returns_it_as_json_key(home, monkeypatch):
    core.save_full("acme-api", "two things", pending=[{"text": "deploy", "severity": 3}])
    monkeypatch.setenv("MEMORY_BOOST_TODAY", "2026-12-31")
    out = core.brief("acme-api")
    assert "still open?" in out["text"]
    assert [p["id"] for p in out["pending_open"]] == ["acme-api#1"]
    assert core.brief(None)["pending_open"] == []


def test_owner_outside_the_enum_is_rejected():
    with pytest.raises(ValueError):
        core.parse_pending([{"text": "x", "owner": "cliente", "severity": 2}])


def test_closes_ignores_unknown_and_already_closed_ids(home):
    core.save_full("acme-api", "first", pending="a; b")
    core.save_full("acme-api", "close a", closes=["acme-api#1"])
    out = core.save_full("acme-api", "messy close",
                         closes=["acme-api#1", "acme-api#2", "acme-api#2", "acme-api#99"])
    assert out["closes"] == ["acme-api#2"]
    assert out["ignored"] == [
        {"id": "acme-api#1", "why": "already closed"},
        {"id": "acme-api#2", "why": "repeated"},
        {"id": "acme-api#99", "why": "unknown"},
    ]
    assert core.iter_events()[-1]["closes"] == ["acme-api#2"]
    assert "ignored: acme-api#1 (already closed)" in core.save_summary("messy close", out)


def test_drops_ignore_ids_that_are_not_open(home):
    core.save_full("acme-api", "first", pending="a")
    out = core.save_full("acme-api", "drop", drops=[{"id": "acme-api#7", "why": "x"},
                                                    {"id": "acme-api#1", "why": "ya no aplica"}])
    assert [d["id"] for d in core.iter_events()[-1]["drops"]] == ["acme-api#1"]
    assert out["ignored"] == [{"id": "acme-api#7", "why": "unknown"}]


def test_clean_save_has_no_ignored_ids(home):
    core.save_full("acme-api", "first", pending="a")
    out = core.save_full("acme-api", "close", closes=["acme-api#1"])
    assert out["ignored"] == []
    assert "ignored" not in core.save_summary("close", out)


def test_done_check_is_kept_and_shown_in_open_pending(home):
    items = core.parse_pending([{"text": "lint", "owner": "agent", "done_check": "ruff check src"}])
    assert items[0]["done_check"] == "ruff check src"
    lint = {"text": "lint", "owner": "agent", "done_check": "ruff check src"}
    core.save_full("acme-api", "first", pending=[lint, {"text": "otro"}])
    open_ = {p["id"]: p for p in core.open_pending("acme-api")}
    assert open_["acme-api#1"]["done_check"] == "ruff check src"
    assert open_["acme-api#2"]["done_check"] is None


@pytest.mark.parametrize("bad", ["", "  ", 3, ["ls"]])
def test_done_check_must_be_a_non_empty_string(bad):
    with pytest.raises(ValueError):
        core.parse_pending([{"text": "x", "done_check": bad}])
