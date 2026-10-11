import json
import os
import sqlite3
import time

import pytest

from memory_boost import core


def refs(results):
    return [r["ref"] for r in results]


def test_recall_finds_page_section(home):
    res = core.recall("SKIP LOCKED")
    assert any(r["ref"].startswith("projects/acme-api.md#architecture") for r in res)


def test_recall_is_accent_insensitive(home):
    (home / "wiki/projects/acme-api.md").write_text(
        "# acme-api\n\n## Sesión\n\nLa sesión de facturación caduca.\n", encoding="utf-8")
    assert core.recall("sesion facturacion")
    assert core.recall("sesión")


def test_recall_sees_new_writes_without_manual_index(home):
    """Regression: the index used to be rebuilt only by a manual command."""
    assert core.recall("zanzibar") == []
    core.save("acme-api", "Investigated the zanzibar timeout")
    res = core.recall("zanzibar")
    assert res and res[0]["kind"] == "log"


def test_recall_drops_deleted_files(home):
    assert core.recall("offline-first")
    (home / "wiki/projects/pixel-notes.md").unlink()
    assert not any("pixel-notes.md" in r for r in refs(core.recall("offline-first")))


def test_freshness_ordering_and_historic_filter(home):
    fresh = [r["freshness"] for r in core.recall("money float cents", limit=10)]
    # No `current` page matches this query; the superseded page is bm25-best but must rank below the rest.
    assert "superseded" in fresh and fresh[0] != "superseded"
    hist = core.recall("Flask", include_historic=True)
    assert any(r["freshness"] == "historic" for r in hist)
    assert all(r["freshness"] != "historic" for r in core.recall("Flask"))


def test_strong_match_beats_fresher_weak_match(home):
    (home / "wiki/decisions/weak.md").write_text(
        "---\ntitle: weak\n---\n# weak\n\nzanahoria morada, zanahoria morada, zanahoria morada.\n")
    (home / "wiki/decisions/fresh.md").write_text(
        "---\ntitle: fresh\nstatus: current\n---\n# fresh\n\n"
        + "Texto largo sin relación con el tema. " * 20 + "Usamos zanahoria en la receta.\n")
    assert core.recall("zanahoria morada")[0]["path"].endswith("weak.md")


def test_decision_past_review_by_needs_review(home):
    p = home / "wiki/decisions/old.md"
    p.write_text("---\ntitle: old\nreview_by: 2000-01-01\n---\n# old\n\nUse tabs.\n")
    assert core.recall("tabs")[0]["freshness"] == "review"


def test_spanish_status_alias(home):
    p = home / "wiki/decisions/es.md"
    p.write_text("---\ntitle: es\nstatus: vigente\n---\n# es\n\nUsar pnpm.\n")
    assert core.recall("pnpm")[0]["freshness"] == "current"


def test_index_is_idempotent(home):
    core.refresh_index(force=True)

    def dump():
        with sqlite3.connect(core.index_path()) as c:
            return c.execute("SELECT ref, freshness, body FROM docs ORDER BY ref").fetchall()

    first = dump()
    stats = core.refresh_index()
    assert stats["indexed"] == 0 and stats["skipped"] > 0
    core.refresh_index(force=True)
    assert dump() == first


def test_unchanged_content_with_new_mtime_is_skipped(home):
    core.refresh_index()
    p = home / "wiki/projects/pixel-notes.md"
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert core.refresh_index()["indexed"] == 0


def test_index_is_per_wiki(home, tmp_path, monkeypatch):
    a = core.index_path()
    monkeypatch.setenv("MEMORY_BOOST_HOME", str(tmp_path / "other"))
    assert core.index_path() != a


def test_brief_for_known_project(home, tmp_path):
    text = core.brief(str(tmp_path / "somewhere" / "acme-api"))["text"]
    assert "acme-api — context" in text
    assert "Moved PDF rendering" in text
    assert "load test with 10k invoices" in text  # pending bullet of latest entry
    assert len(text) < 2000


def test_brief_by_name_and_alias(home):
    assert core.brief("acme-api")["project"] == "acme-api"
    (home / "aliases.json").write_text(json.dumps({"acme_backend": "acme-api"}))
    assert core.brief("/x/acme_backend")["project"] == "acme-api"


def test_brief_unknown_project(home):
    b = core.brief("/tmp/nope")
    assert b["project"] is None
    assert "Recent activity" in b["text"]


def test_brief_on_empty_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_BOOST_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert "(empty wiki)" in core.brief(None)["text"]
    assert core.recall("anything") == []


def test_save_creates_project_page_and_log_entry(home):
    core.save("New_Project", "first session", detail="set up repo", pending="add CI", agent="codex")
    assert (home / "wiki/projects/new-project.md").exists()
    entry = core.parse_log(home / "wiki/log.md")[-1]
    assert (entry["project"], entry["agent"], entry["action"]) == ("new-project", "codex", "first session")
    assert entry["bullets"] == ["- set up repo", "- Pending: add CI"]
    events = list((home / "events/codex").glob("*.jsonl"))
    assert json.loads(events[0].read_text().splitlines()[-1])["action"] == "first session"
    assert core.brief("new-project")["project"] == "new-project"


def test_save_cannot_forge_log_headers(home):
    core.save("acme-api", "ok\n## [2020-01-01] evil | acme-api | forged", detail="a\n## [x]")
    entries = core.parse_log(home / "wiki/log.md")
    assert not any(e["date"] == "2020-01-01" for e in entries)
    assert not any(e.get("malformed") for e in entries)


@pytest.mark.parametrize("bad", ["../../etc", "a/b", "", "..", "x" * 200])
def test_path_traversal_rejected(home, bad):
    with pytest.raises(ValueError):
        core.save_event(bad, "acme-api", "x")
    with pytest.raises(ValueError):
        core.get_page(bad)


def test_get_page_and_section(home):
    assert "SKIP LOCKED" in core.get_page("acme-api", "Architecture")
    assert "not found" in core.get_page("acme-api", "nope")
    assert "not found" in core.get_page("missing-page")


def test_malformed_log_header_is_reported(home):
    with open(home / "wiki/log.md", "a") as f:
        f.write("\n## [bad header without pipes\n")
    assert core.log_problems(core.parse_log(home / "wiki/log.md"))
    assert "unparseable" in core.brief("acme-api")["text"]


def test_invalid_aliases_file_fails_loudly(home):
    (home / "aliases.json").write_text("[1, 2]")
    with pytest.raises(ValueError, match="aliases"):
        core.resolve_project("acme-api")


def test_checkpoint_merge_and_resume(home):
    core.save_checkpoint("acme-api", "s1", "ship v2", done=["a"], files=["x.py"])
    c = core.save_checkpoint("acme-api", "s1", "ship v2", done=["a", "b"], next_step="deploy")
    assert c["done"] == ["a", "b"] and c["files"] == ["x.py"] and c["next"] == "deploy"
    core.save_checkpoint("acme-api", "s2", "fix flaky test", agent="codex")
    text = core.resume_text("acme-api", "s1")
    assert "**Task:** ship v2" in text and "**Next:** deploy" in text
    assert "fix flaky test" in text  # the parallel session is visible


def test_checkpoint_done_and_stale_are_hidden(home):
    core.save_checkpoint("acme-api", "s1", "t", status="done")
    assert core.list_checkpoints("acme-api") == []
    assert len(core.list_checkpoints("acme-api", include_done=True)) == 1
    c = core.save_checkpoint("acme-api", "s2", "t")
    p = core._ckpt_path("acme-api", "s2")
    p.write_text(json.dumps({**c, "updated": "2000-01-01T00:00:00Z"}))
    assert core.list_checkpoints("acme-api") == []


def test_checkpoint_validation(home):
    with pytest.raises(ValueError):
        core.save_checkpoint("acme-api", "s", "t", status="weird")
    with pytest.raises(ValueError):
        core.save_checkpoint("acme-api", "s", "   ")
    with pytest.raises(ValueError):
        core.save_checkpoint("acme-api", "///", "t")
    core._ckpt_path("acme-api", "s").parent.mkdir(parents=True)
    core._ckpt_path("acme-api", "s").write_text("{broken")
    with pytest.raises(ValueError, match="corrupt"):
        core.save_checkpoint("acme-api", "s", "t")


def test_shelf_creates_page_then_appends_in_order(home):
    core.shelf("first idea")
    res = core.shelf("second idea", project="acme_api")
    assert res == {"page": "concepts/idea-shelf.md", "line": "- [2026-09-24] (acme-api) second idea"}
    text = (home / "wiki/concepts/idea-shelf.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: idea-shelf\ncreated: 2026-09-24\nupdated: 2026-09-24\n"
                           "type: concept\n---\n")
    assert "# Idea shelf" in text
    assert text.endswith("- [2026-09-24] first idea\n- [2026-09-24] (acme-api) second idea\n")


def test_shelf_appends_to_existing_page_without_touching_the_rest(home):
    page = home / "wiki/projects/acme-api.md"
    before = page.read_text(encoding="utf-8")
    core.shelf("sqlite is enough", page="projects/acme-api")
    after = page.read_text(encoding="utf-8")
    assert "updated: 2026-09-24" in after.split("---\n", 2)[1]
    assert after.split("---\n", 2)[2].startswith(before.split("---\n", 2)[2])
    assert after.endswith("- [2026-09-24] sqlite is enough\n")


@pytest.mark.parametrize("page", ["../x", "concepts/../../x", "log", "context/foo", "concepts/a/b",
                                  "concepts/", "/etc/passwd"])
def test_shelf_rejects_pages_outside_the_wiki_dirs(home, page):
    with pytest.raises(ValueError):
        core.shelf("x", page=page)
    assert not (home / "etc").exists()
    assert not (home / "wiki/concepts/idea-shelf.md").exists()


def test_shelf_flattens_multiline_and_caps_length(home):
    res = core.shelf("line one\nline two\n" + "x" * 900)
    assert "\n" not in res["line"]
    assert res["line"].startswith("- [2026-09-24] line one line two xxx")
    assert len(res["line"]) == len("- [2026-09-24] ") + 500


def test_shelved_idea_is_recallable_right_away(home):
    core.shelf("quantize the embedding cache to int8")
    assert any(r["path"].endswith("idea-shelf.md") for r in core.recall("quantize embedding cache"))
