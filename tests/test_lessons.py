import pytest

from memory_boost import core, lessons


def test_example_lessons_travel_between_projects(home):
    for_acme = lessons.lessons_for("acme-api")
    assert [lesson["name"] for lesson in for_acme] == ["retry-jobs-idempotently"]
    assert for_acme[0]["matched"] == ["background-jobs"] and for_acme[0]["learned_on"] == "pixel-notes"
    for_pixel = lessons.lessons_for("pixel_notes")
    assert [lesson["name"] for lesson in for_pixel] == ["money-as-integer-cents"]


def test_own_lessons_are_not_echoed_back(home):
    names = {lesson["name"] for lesson in lessons.lessons_for("acme-api")}
    assert "money-as-integer-cents" not in names  # learned_on: acme-api


def test_project_without_tags_or_page_gets_nothing(home):
    assert lessons.lessons_for("nope") == []
    p = core.wiki_dir() / "projects" / "acme-api.md"
    p.write_text(p.read_text().replace("tags: [python, fastapi, postgres, background-jobs]\n", ""))
    assert lessons.lessons_for("acme-api") == []


def test_concept_without_applies_when_is_not_a_lesson(home):
    (core.wiki_dir() / "concepts" / "glossary.md").write_text(
        "---\ntitle: Glossary\n---\n# Glossary\n\nterms\n")
    assert "glossary" not in {lesson["name"] for lesson in lessons.all_lessons()}


def test_save_lesson_roundtrip_and_ranking(home):
    p = lessons.save_lesson("pool-size", "Size the DB pool from worker count", ["postgres", "fastapi"],
                            "Pool = workers × 2 + spare.\n", learned_on="pixel-notes")
    assert p == core.wiki_dir() / "concepts" / "pool-size.md"
    meta, body = core.parse_frontmatter(p.read_text())
    assert meta["applies_when"] == "[fastapi, postgres]" and meta["learned_on"] == "pixel-notes"
    found = lessons.lessons_for("acme-api")
    assert found[0]["name"] == "pool-size" and found[0]["matched"] == ["fastapi", "postgres"]
    line = lessons.format_lessons(found)[0]
    assert line.startswith("- Size the DB pool") and 'memory_page("pool-size")' in line
    assert core.recall("pool workers")[0]["path"] == "concepts/pool-size.md"


@pytest.mark.parametrize("name,tags,body", [("../evil", ["a"], "b"), ("ok", [], "b"), ("ok", ["a"], "  ")])
def test_save_lesson_rejects_bad_input(home, name, tags, body):
    with pytest.raises(ValueError):
        lessons.save_lesson(name, "t", tags, body)


def test_brief_injects_drift_and_lessons(home):
    text = core.brief("acme-api")["text"]
    assert "### acme-api — drift\n- decision-expired: pin-postgres-15" in text
    assert "### Lessons from other projects\n- Make background jobs idempotent" in text
    other = core.brief("pixel-notes")["text"]
    assert "decision-expired: pin-postgres-15" not in other and "- project-no-decisions: pixel-notes" in other
