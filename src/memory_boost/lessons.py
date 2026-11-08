"""Lessons that travel between projects.

A lesson is a page in wiki/concepts/ with `applies_when: [tag, ...]` in its
frontmatter. A project page declares `tags: [...]`. brief() surfaces the lessons
whose applies_when overlaps the current project's tags and that were NOT learned
on this project, so what worked in one place reaches the next.

Optional evidence fields (after Harness², arXiv 2610.14224): `cue` is the shape of
situation the lesson fires on, `evidence` the observed contrast it rests on, and
`confidence` high | medium | low. A lesson without evidence still travels, but it is
shown as unproven and drift reports it: an invented attribution misleads every later
project that reads it."""
from __future__ import annotations

from pathlib import Path

from memory_boost import core
from memory_boost.drift import parse_tags

CONFIDENCE = ("high", "medium", "low")


def project_tags(project: str) -> set[str]:
    p = core.wiki_dir() / "projects" / f"{core.norm_project(project)}.md"
    if not p.exists():
        return set()
    meta, _ = core.parse_frontmatter(p.read_text(encoding="utf-8"))
    return parse_tags(meta)


def all_lessons() -> list[dict]:
    out = []
    for p in sorted((core.wiki_dir() / "concepts").glob("*.md")):
        meta, body = core.parse_frontmatter(p.read_text(encoding="utf-8"))
        applies = parse_tags(meta, "applies_when")
        if not applies:
            continue            # a concept page without applies_when is reference, not a lesson
        summary = next((ln.strip() for ln in body.splitlines()
                        if ln.strip() and not ln.startswith("#")), "")
        out.append({"name": p.stem, "title": meta.get("title", p.stem), "applies_when": applies,
                    "learned_on": core.norm_project(meta.get("learned_on", "")),
                    "date": meta.get("updated") or meta.get("created"), "summary": summary,
                    "cue": meta.get("cue", ""), "evidence": meta.get("evidence", ""),
                    "confidence": meta.get("confidence", "")})
    return out


def lessons_for(project: str, limit: int = 3) -> list[dict]:
    """Lessons from other projects whose applies_when matches this project's tags,
    best overlap first."""
    tags = project_tags(project)
    slug = core.norm_project(project)
    if not tags:
        return []
    scored = []
    for lesson in all_lessons():
        overlap = lesson["applies_when"] & tags
        if overlap and lesson["learned_on"] != slug:
            scored.append((len(overlap), lesson["date"] or "", {**lesson, "matched": sorted(overlap)}))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [lesson for _, _, lesson in scored[:limit]]


def _trust(lesson: dict) -> str:
    if not lesson.get("evidence"):
        return "unproven"
    return f"confidence {lesson['confidence']}" if lesson.get("confidence") else "evidence, no confidence"


def format_lessons(lessons: list[dict]) -> list[str]:
    return [f"- {lesson['title']} (from {lesson['learned_on'] or '?'}, matches "
            f"{', '.join(lesson['matched'])}; {_trust(lesson)}) — {lesson['summary'][:160]} "
            f"→ memory_page(\"{lesson['name']}\")"
            for lesson in lessons]


def save_lesson(name: str, title: str, applies_when: list[str], body: str,
                learned_on: str | None = None, cue: str | None = None,
                evidence: str | None = None, confidence: str | None = None) -> Path:
    """Write (or overwrite) wiki/concepts/<name>.md as a lesson."""
    slug = core.safe_slug(name, "lesson name")
    tags = sorted({core.norm_project(t) for t in applies_when if t.strip()})
    if not tags:
        raise ValueError("applies_when is empty: a lesson needs at least one tag to travel")
    if not body.strip():
        raise ValueError("body is empty")
    if confidence is not None and confidence not in CONFIDENCE:
        raise ValueError(f"invalid confidence: {confidence!r} ({'|'.join(CONFIDENCE)})")
    if confidence and not (evidence or "").strip():
        raise ValueError("confidence without evidence: cite the observed contrast it rests on")
    p = core.wiki_dir() / "concepts" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = [f"title: {core._one_line(title) or slug}", f"created: {core._today()}",
            f"applies_when: [{', '.join(tags)}]"]
    if learned_on:
        meta.append(f"learned_on: {core.safe_slug(learned_on, 'learned_on')}")
    for key, value in (("cue", cue), ("evidence", evidence), ("confidence", confidence)):
        if value and value.strip():
            meta.append(f"{key}: {core._one_line(value)}")
    p.write_text("---\n" + "\n".join(meta) + f"\n---\n# {core._one_line(title) or slug}\n\n"
                 + body.strip() + "\n", encoding="utf-8")
    return p
