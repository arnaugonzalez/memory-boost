"""memory-boost CLI: the same operations as the MCP tools, plus `init`, `index`,
`serve` and the harness hook. The hook path imports only the stdlib so session
start stays instant.

Contract for agents: data on stdout, warnings and errors on stderr, `--json` on
every reading command (always with a "version" field), exit 0 = stdout is
trustworthy, 1 = nothing found / nothing to read, 2 = usage or invalid input."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from memory_boost import __version__, core

JSON_VERSION = 1
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2

EXAMPLES = """\
examples:
  memory-boost init --example                 seed a sample wiki + synthetic transcripts
  memory-boost drift                          what in the wiki expired, went silent or was never written
  memory-boost mine --since 7d                digest of your local Claude Code transcripts (aggregates)
  memory-boost brief --project acme-api       what an agent gets at session start (add --json)
  memory-boost recall "retry jobs" --json     full-text search, machine-readable
  memory-boost lessons --project acme-api     lessons from *other* projects that apply here
  memory-boost page queue-in-postgres         print a page; exit 1 if it does not exist

exit codes: 0 ok · 1 not found / nothing to read · 2 usage or invalid input
"""


def _json_out(obj) -> None:
    """Every JSON result carries the schema version so agents can pin it."""
    if isinstance(obj, list):
        obj = {"results": obj}
    print(json.dumps({"version": JSON_VERSION, **obj}, ensure_ascii=False, indent=2,
                     default=lambda v: sorted(v) if isinstance(v, set) else str(v)))


def _fail(msg: str, fix: str | None = None, code: int = EXIT_NOT_FOUND) -> None:
    print(f"error: {msg}", file=sys.stderr)
    if fix:
        print(f"fix: {fix}", file=sys.stderr)
    sys.exit(code)


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def _hook_envelope(text: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": text}}, ensure_ascii=False)


def session_start_payload(event: dict) -> str | None:
    """Text to inject for a SessionStart event ({cwd, session_id, source}).
    After a compaction the brief is already in the summary: only the exact
    checkpoint of this session is missing, so that is all we inject."""
    cwd = event.get("cwd") or str(Path.cwd())
    sid = event.get("session_id") or None
    project = core.resolve_project(cwd)
    if event.get("source") == "compact":
        return core.resume_text(project, sid) if project else None

    result = core.brief(cwd)
    text = result["text"]
    if sid:
        text += (f"\n\ncheckpoint_session={sid} — after each milestone call memory_checkpoint("
                 "project, session_id=this, task, done, next_step, files); it survives compaction "
                 "and parallel sessions can see it.")
    if project:
        own = core.load_checkpoint(project, sid) if sid else None
        if own and own.get("status") != "done":
            text += "\n\n" + core.resume_text(project, sid, budget=900)
        else:
            others = [c for c in core.list_checkpoints(project) if c["session_id"] != sid]
            if others:
                text += "\n\nOther active sessions on this project:\n" + "\n".join(
                    core.format_other_sessions(others))
    return text


def cmd_hook(args):
    # A hook must never block or break session start: any failure -> no output, exit 0.
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        text = session_start_payload(event)
    except Exception as e:  # noqa: BLE001 — logged to stderr, never raised into the harness
        print(f"memory-boost hook: {e}", file=sys.stderr)
        return
    if text:
        print(_hook_envelope(text) if args.format == "claude" else text)


def cmd_init(args):
    home = core.home()
    wiki = core.wiki_dir()
    if args.example:
        if any(wiki.glob("**/*.md")):
            _fail(f"{wiki} is not empty; --example only seeds an empty wiki",
                  fix="memory-boost init   # without --example", code=EXIT_USAGE)
        src = resources.files("memory_boost") / "example_wiki"
        with resources.as_file(src) as p:
            shutil.copytree(p, wiki, dirs_exist_ok=True)
        src = resources.files("memory_boost") / "example_transcripts"
        with resources.as_file(src) as p:
            shutil.copytree(p, home / "example_transcripts", dirs_exist_ok=True)
    for sub in ("projects", "decisions", "concepts", "context"):
        (wiki / sub).mkdir(parents=True, exist_ok=True)
    (wiki / "log.md").touch()
    print(f"memory home: {home}\nwiki:        {wiki}")
    if args.example:
        print(f"next:        memory-boost drift\n"
              f"             memory-boost mine --root {home / 'example_transcripts'}")


def cmd_brief(args):
    result = core.brief(args.project or args.cwd or str(Path.cwd()), budget=args.budget)
    _json_out(result) if args.json else print(result["text"])


def cmd_recall(args):
    results = core.recall(args.query, project=args.project, limit=args.limit,
                          include_historic=args.include_historic)
    if args.json:
        _json_out(results)
        return
    if not results:
        _fail(f"no results for {args.query!r}",
              fix="memory-boost recall <fewer words>   # or --include-historic")
    for r in results:
        print(f"[{r['kind']} | {r['date'] or '?'} | {r['freshness']}] {r['ref']} — {r['extract']}")


def cmd_page(args):
    found = core.page_path(args.name)
    if not found:
        _fail(f"page '{args.name}' not found", fix="memory-boost recall <words from the title>")
    text = core.get_page(args.name, args.section)
    if args.section and text.startswith("(section '"):
        _fail(text.strip("()"), fix=f"memory-boost page {args.name}   # whole page")
    if args.json:
        _json_out({"name": args.name, "kind": found[0], "section": args.section, "text": text})
    else:
        print(text)


def cmd_save(args):
    slug = core.save(args.project, args.action, detail=args.detail, result=args.result,
                     agent=args.agent, pending=args.pending)
    print(f"saved: {slug} | {args.action}")


def _split(v):
    return [x.strip() for x in v.split("||") if x.strip()] if v else None


def cmd_checkpoint(args):
    c = core.save_checkpoint(args.project, args.session, args.task, agent=args.agent,
                             done=_split(args.done), next_step=args.next, files=_split(args.files),
                             notes=_split(args.notes), status=args.status,
                             plan_ref=args.plan_ref, replace_done=args.replace_done)
    print(f"checkpoint: {c['project']} | {c['session_id']} | {c['status']} | done={len(c['done'])}")


def cmd_resume(args):
    if args.json:
        own = core.load_checkpoint(args.project, args.session) if args.session else None
        others = [c for c in core.list_checkpoints(args.project) if c["session_id"] != args.session]
        _json_out({"project": args.project, "checkpoint": own, "others": others})
    else:
        print(core.resume_text(args.project, args.session, budget=args.budget))


def cmd_checkpoints(args):
    items = core.list_checkpoints(args.project, include_done=args.all)
    if args.json:
        _json_out(items)
        return
    for c in items:
        print(f"[{c['project']} | {c['agent']} | {c['status']} | {c['updated'][:16]}] "
              f"{c['session_id']}: {c['task']}")


def cmd_index(args):
    stats = core.refresh_index(force=args.rebuild)
    for w in stats.pop("warnings"):
        _warn(w)
    _json_out(stats)


def cmd_drift(args):
    from memory_boost import drift
    wiki = core.wiki_dir()
    if not wiki.is_dir():
        _fail(f"wiki directory {wiki} does not exist", fix="memory-boost init --example")
    report = drift.drift_report()
    if args.json:
        _json_out(report)
    elif args.project:
        print("\n".join(drift.for_project(args.project, n=50)) or "(nothing drifted)")
    else:
        print(drift.format_report(report), end="")


def cmd_mine(args):
    from memory_boost import mine
    roots = [Path(r).expanduser() for r in args.root] if args.root else mine.transcript_roots()
    missing = [r for r in roots if not r.is_dir()]
    for r in missing:
        _warn(f"transcript root {r} does not exist")
    if len(missing) == len(roots):
        _fail("no transcript directory to read",
              fix="memory-boost mine --root ~/.claude/projects   # or set MEMORY_BOOST_TRANSCRIPTS")
    since = None
    if args.since:
        days = int(args.since.rstrip("d"))
        since = (dt.date.fromisoformat(core._today()) - dt.timedelta(days=days)).isoformat()
    agg = mine.aggregate(mine.scan(roots, since=since, min_turns=args.min_turns))
    if args.json:
        _json_out(agg)
    else:
        print(mine.format_digest(agg, since), end="")


def cmd_lessons(args):
    from memory_boost import lessons
    found = lessons.lessons_for(args.project, limit=args.limit) if args.project else lessons.all_lessons()
    if args.json:
        _json_out(found)
        return
    if args.project:
        print("\n".join(lessons.format_lessons(found)) or f"(no lessons match {args.project})")
    else:
        for lesson in found:
            print(f"{lesson['name']} [{', '.join(sorted(lesson['applies_when']))}] "
                  f"from {lesson['learned_on'] or '?'} — {lesson['summary'][:100]}")


def cmd_lesson(args):
    from memory_boost import lessons
    body = args.body if args.body != "-" else sys.stdin.read()
    p = lessons.save_lesson(args.name, args.title, args.applies_when.split(","), body,
                            learned_on=args.learned_on)
    print(f"lesson saved: {p}")


def cmd_serve(_args):
    from memory_boost.server import main as serve  # imports `mcp` only when serving
    serve()


def _json_flag(s: argparse.ArgumentParser) -> None:
    s.add_argument("--json", action="store_true", help="machine-readable output (with 'version')")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memory-boost",
                                description="Memory that reviews itself: a markdown wiki for coding "
                                            "agents, plus drift reports and session digests.",
                                epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create the memory home (MEMORY_BOOST_HOME)")
    s.add_argument("--example", action="store_true",
                   help="seed an empty wiki with the example wiki and four synthetic transcripts")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("serve", help="run the MCP server on stdio")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("hook", help="harness hook entry point (reads the event JSON on stdin)")
    s.add_argument("event", choices=["session-start"])
    s.add_argument("--format", choices=["claude", "text"], default="claude",
                   help="claude = hookSpecificOutput envelope; text = plain context")
    s.set_defaults(func=cmd_hook)

    s = sub.add_parser("brief", help="orientation for a project or directory (what the hook injects)")
    s.add_argument("--project", default=None, help="project slug; default: resolve from --cwd")
    s.add_argument("--cwd", default=None, help="directory to resolve the project from (default: .)")
    s.add_argument("--budget", type=int, default=1200, help="approximate size in characters")
    _json_flag(s)
    s.set_defaults(func=cmd_brief)

    s = sub.add_parser("recall", help="full-text search over the wiki and log")
    s.add_argument("query", help="words to search (FTS5 syntax allowed)")
    s.add_argument("--project", default=None, help="restrict to one project")
    s.add_argument("--limit", type=int, default=8, help="max results")
    s.add_argument("--include-historic", action="store_true",
                   help="also return historic / superseded pages")
    _json_flag(s)
    s.set_defaults(func=cmd_recall)

    s = sub.add_parser("page", help="print a wiki page or one section (exit 1 if missing)")
    s.add_argument("name", help="page slug, e.g. acme-api or queue-in-postgres")
    s.add_argument("--section", default=None, help="only this section (by title)")
    _json_flag(s)
    s.set_defaults(func=cmd_page)

    s = sub.add_parser("save", help="record a session event + log.md entry")
    s.add_argument("--project", required=True, help="project slug")
    s.add_argument("--action", required=True, help="one line: what was done")
    s.add_argument("--agent", default="cli", help="who did it (default: cli)")
    s.add_argument("--result", default="ok", help="ok | partial | failed")
    s.add_argument("--detail", default=None, help="longer markdown detail")
    s.add_argument("--pending", default=None, help="what is left to do")
    s.set_defaults(func=cmd_save)

    s = sub.add_parser("checkpoint", help="save/merge a session checkpoint (lists split by '||')")
    s.add_argument("--project", required=True, help="project slug")
    s.add_argument("--session", required=True, help="session id (from the hook)")
    s.add_argument("--task", required=True, help="what this session is doing")
    s.add_argument("--agent", default="cli", help="who is working (default: cli)")
    s.add_argument("--done", default=None, help="steps done, split by '||'")
    s.add_argument("--next", default=None, help="the next step")
    s.add_argument("--files", default=None, help="files touched, split by '||'")
    s.add_argument("--notes", default=None, help="notes, split by '||'")
    s.add_argument("--status", default="active", choices=core.CHECKPOINT_STATUSES)
    s.add_argument("--plan-ref", default=None, help="path or URL of the plan")
    s.add_argument("--replace-done", action="store_true", help="replace the done list instead of merging")
    s.set_defaults(func=cmd_checkpoint)

    s = sub.add_parser("resume", help="print a session checkpoint + other active sessions")
    s.add_argument("--project", required=True, help="project slug")
    s.add_argument("--session", default=None, help="session id; omit for others only")
    s.add_argument("--budget", type=int, default=1200, help="approximate size in characters")
    _json_flag(s)
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("checkpoints", help="list active checkpoints")
    s.add_argument("--project", default=None, help="restrict to one project")
    s.add_argument("--all", action="store_true", help="include finished ones")
    _json_flag(s)
    s.set_defaults(func=cmd_checkpoints)

    s = sub.add_parser("drift", help="what in the wiki is stale, silent or never written down")
    s.add_argument("--project", default=None, help="only this project's findings, as bullet lines")
    _json_flag(s)
    s.set_defaults(func=cmd_drift)

    s = sub.add_parser("mine", help="digest of your local agent transcripts (aggregates only)")
    s.add_argument("--root", action="append", default=None,
                   help="transcript directory (repeatable; default MEMORY_BOOST_TRANSCRIPTS "
                        "or ~/.claude/projects)")
    s.add_argument("--since", default=None, help="only sessions newer than e.g. 7d, 30d")
    s.add_argument("--min-turns", type=int, default=20, help="ignore shorter sessions")
    _json_flag(s)
    s.set_defaults(func=cmd_mine)

    s = sub.add_parser("lessons", help="list lessons, or those that apply to a project")
    s.add_argument("--project", default=None, help="lessons from other projects matching its tags")
    s.add_argument("--limit", type=int, default=3, help="max lessons with --project")
    _json_flag(s)
    s.set_defaults(func=cmd_lessons)

    s = sub.add_parser("lesson", help="record a lesson that travels to other projects")
    s.add_argument("--name", required=True, help="slug, e.g. retry-jobs-idempotently")
    s.add_argument("--title", required=True, help="one imperative line")
    s.add_argument("--applies-when", required=True, help="comma-separated tags")
    s.add_argument("--learned-on", default=None, help="project slug it was learned on")
    s.add_argument("--body", required=True, help="markdown body, or - for stdin")
    s.set_defaults(func=cmd_lesson)

    s = sub.add_parser("index", help="refresh the search index (automatic on recall)")
    s.add_argument("--rebuild", action="store_true", help="drop and rebuild from scratch")
    s.add_argument("--json", action="store_true", help="(always JSON; accepted for symmetry)")
    s.set_defaults(func=cmd_index)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ValueError as e:
        _fail(str(e), fix="memory-boost <command> --help", code=EXIT_USAGE)


if __name__ == "__main__":
    main()
