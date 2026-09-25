"""Core logic: FTS5 index over a markdown wiki, freshness ("vigencia") rules,
the cheap session brief, event/log writes and session checkpoints.

Shared by the CLI (hooks) and the MCP server so that "cheap recall at session
start" and "recall on demand" never drift apart. Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

# --- locations -------------------------------------------------------------
#
# Everything lives under MEMORY_BOOST_HOME (default $XDG_DATA_HOME/memory-boost):
#   wiki/          compiled pages (projects/, decisions/, concepts/, context/, log.md)
#   events/        raw append-only JSONL, one dir per agent
#   checkpoints/   per-session working state (JSON), survives context compaction
#   aliases.json   optional {"dir-name": "project-slug"} map
# The FTS index is a disposable cache under $XDG_CACHE_HOME, keyed by wiki path.
# Paths are resolved on every call so env changes (tests, several homes) apply.


def plural(n: int, word: str, many: str | None = None) -> str:
    """'1 finding', '2 findings' — reports are read by people too."""
    return f"{n} {word}" if n == 1 else f"{n} {many or word + 's'}"


def home() -> Path:
    if env := os.environ.get("MEMORY_BOOST_HOME"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "memory-boost"


def wiki_dir() -> Path:
    env = os.environ.get("MEMORY_BOOST_WIKI")
    return Path(env).expanduser() if env else home() / "wiki"


def events_dir() -> Path:
    return home() / "events"


def checkpoints_dir() -> Path:
    env = os.environ.get("MEMORY_BOOST_CHECKPOINTS")
    return Path(env).expanduser() if env else home() / "checkpoints"


def index_path() -> Path:
    if env := os.environ.get("MEMORY_BOOST_INDEX"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    key = hashlib.sha256(str(wiki_dir().resolve()).encode()).hexdigest()[:12]
    return Path(base) / "memory-boost" / f"index-{key}.db"


def aliases_path() -> Path:
    return home() / "aliases.json"


# --- parsing ---------------------------------------------------------------

LOG_ENTRY_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})([a-z])?\]\s*(\S+)\s*\|\s*([^|]+?)\s*\|\s*(.+)$")
LOG_HEADER_LOOSE_RE = re.compile(r"^## \[")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
# Markers authors write into prose to flag a section as no longer current.
SUPERSEDED_RE = re.compile(
    r"\bsuperseded\b|\bobsolete\b|\bdeprecated\b|\bno longer applies\b|\boutdated\b"
    r"|desactualizad|obsolet|superad[oa]|ya no aplica|deprecad", re.I)
HISTORY_SUFFIXES = ("-history", "-historial")

FRESHNESS = ("current", "unclassified", "review", "historic", "superseded")
FRESHNESS_ORDER = {v: i for i, v in enumerate(FRESHNESS)}
# Frontmatter `status:` values accepted as aliases (wikis written in Spanish).
STATUS_ALIASES = {"vigente": "current", "revisar": "review", "historico": "historic",
                  "histórico": "historic"}

# How many of a project's most recent log entries count as "current": used
# both to classify log chunks and to cap the recent activity in brief().
CURRENT_LOG_ENTRIES_PER_PROJECT = 3

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def norm_project(name: str) -> str:
    """my_api / my-api / MY_API -> my-api"""
    return slugify(name.replace("_", "-"))


def safe_slug(value: str, field: str) -> str:
    """Normalise a user/LLM-supplied name used in a path; reject anything that
    does not survive as a plain slug (no separators, no traversal)."""
    s = norm_project(value)
    if not SLUG_RE.match(s) or any(sep in value for sep in ("/", "\\", "..")):
        raise ValueError(f"invalid {field}: {value!r} (use letters, digits, '-' or '_')")
    return s


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _is_history(stem: str) -> bool:
    return stem.endswith(HISTORY_SUFFIXES)


def _today() -> str:
    """UTC date; MEMORY_BOOST_TODAY=YYYY-MM-DD pins it for reproducible reports and tests."""
    return os.environ.get("MEMORY_BOOST_TODAY") or dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_aliases() -> dict:
    p = aliases_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{p}: invalid JSON ({e})") from e
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected an object {{\"dir-name\": \"project\"}}")
    return {norm_project(k): norm_project(v) for k, v in data.items()}


def resolve_project(cwd_or_name: str) -> str | None:
    """basename(cwd) or a project name -> slug that has a page in wiki/projects/."""
    base = norm_project(Path(cwd_or_name).name)
    base = load_aliases().get(base, base)
    if base and not _is_history(base) and (wiki_dir() / "projects" / f"{base}.md").exists():
        return base
    return None


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta, text[m.end():]


def _status(meta: dict) -> str | None:
    s = (meta.get("status") or "").lower()
    s = STATUS_ALIASES.get(s, s)
    return s if s in FRESHNESS else None


def _freshness(kind: str, title: str | None, body: str, meta: dict, is_history: bool) -> str:
    if is_history:
        return "historic"
    if SUPERSEDED_RE.search(body):
        return "superseded"
    t = (title or "").lower()
    if "history" in t or "historial" in t:
        return "historic"
    if kind == "decision":
        if status := _status(meta):
            return status
        review_by = meta.get("review_by")
        if review_by and review_by < _today():
            return "review"
        return "current"
    return "unclassified"


def chunk_page(path: Path, kind: str) -> tuple[list[dict], dict]:
    """Split a page into ##-level chunks. The chunk before the first ## uses anchor 'top'."""
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    is_history = _is_history(path.stem)
    project = None
    if kind == "project":
        project = path.stem
        for suf in HISTORY_SUFFIXES:
            project = project.removesuffix(suf)

    sections: list[tuple[str | None, list[str]]] = []
    cur_title: str | None = None
    cur_lines: list[str] = []
    for line in body.splitlines():
        if hm := H2_RE.match(line):
            sections.append((cur_title, cur_lines))
            cur_title, cur_lines = hm.group(1), []
        else:
            cur_lines.append(line)
    sections.append((cur_title, cur_lines))

    rel = path.relative_to(wiki_dir()).as_posix()
    chunks = []
    for title, lines in sections:
        content = "\n".join(lines).strip()
        if not content and title is None:
            continue
        chunks.append({
            "ref": f"{rel}#{slugify(title) if title else 'top'}",
            "path": rel,
            "kind": kind,
            "project": project,
            "title": title or meta.get("title", path.stem),
            "date": meta.get("updated") or meta.get("created"),
            "freshness": _freshness(kind, title, content, meta, is_history),
            "body": content,
        })
    return chunks, meta


def parse_log(path: Path) -> list[dict]:
    """Parse log.md entries. Headers that look like entries but do not match the
    format are kept and flagged (malformed) instead of being silently merged."""
    if not path.exists():
        return []
    entries: list[dict] = []
    cur = None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if m := LOG_ENTRY_RE.match(line):
            if cur:
                entries.append(cur)
            date, _suffix, agent, project, action = m.groups()
            cur = {"date": date, "agent": agent, "project": norm_project(project),
                   "project_raw": project, "action": action.strip(), "bullets": [],
                   "malformed": False}
        elif LOG_HEADER_LOOSE_RE.match(line):
            if cur:
                entries.append(cur)
            cur = {"date": None, "agent": None, "project": "__malformed__", "project_raw": line,
                   "action": line.strip(), "bullets": [], "malformed": True, "line_no": n}
        elif cur is not None and line.strip():
            cur["bullets"].append(line.strip())
    if cur:
        entries.append(cur)
    entries.sort(key=lambda e: e["date"] or "")
    return entries


def log_problems(entries: list[dict]) -> list[str]:
    return [f"log.md:L{e['line_no']} unparseable header: {e['project_raw']}"
            for e in entries if e.get("malformed")]


def _log_chunks(path: Path) -> list[dict]:
    entries = [e for e in parse_log(path) if not e.get("malformed")]
    per_project: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        per_project.setdefault(e["project"], []).append(i)
    current = {i for idxs in per_project.values() for i in idxs[-CURRENT_LOG_ENTRIES_PER_PROJECT:]}

    rel = path.relative_to(wiki_dir()).as_posix()
    return [{
        "ref": f"{rel}#{e['date']}-{e['project']}-{i}",
        "path": rel,
        "kind": "log",
        "project": e["project"],
        "title": f"{e['date']} {e['project_raw']}: {e['action'][:60]}",
        "date": e["date"],
        "freshness": "current" if i in current else "historic",
        "body": e["action"] + ("\n" + "\n".join(e["bullets"]) if e["bullets"] else ""),
    } for i, e in enumerate(entries)]


# --- index -----------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, content_hash TEXT);
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE, path TEXT, kind TEXT, project TEXT,
  title TEXT, date TEXT, freshness TEXT, body TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, body, content='docs', content_rowid='id',
  tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, title, body) VALUES('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, title, body) VALUES('delete', old.id, old.title, old.body);
  INSERT INTO docs_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
"""

PAGE_DIRS = (("project", "projects"), ("decision", "decisions"),
             ("concept", "concepts"), ("context", "context"))


def _connect() -> sqlite3.Connection:
    p = index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.executescript(SCHEMA)
    return conn


def _wiki_files() -> list[tuple[Path, str]]:
    w = wiki_dir()
    out = [(p, kind) for kind, sub in PAGE_DIRS for p in sorted((w / sub).glob("*.md"))]
    out += [(w / n, "__log__") for n in ("log.md", "log-archive.md") if (w / n).exists()]
    return out


def refresh_index(force: bool = False) -> dict:
    """Incremental: re-chunk only files whose mtime and content hash changed;
    drop rows of deleted files. Cheap enough to run before every recall."""
    conn = _connect()
    stats = {"indexed": 0, "skipped": 0, "chunks": 0}
    warnings: list[str] = []
    seen: set[str] = set()
    today = _today()

    for path, kind in _wiki_files():
        rel = path.relative_to(wiki_dir()).as_posix()
        seen.add(rel)
        mtime = path.stat().st_mtime
        row = conn.execute("SELECT mtime, content_hash FROM files WHERE path=?", (rel,)).fetchone()
        if not force and row and row[0] == mtime:
            stats["skipped"] += 1
            continue
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        if not force and row and row[1] == h:
            conn.execute("UPDATE files SET mtime=? WHERE path=?", (mtime, rel))
            stats["skipped"] += 1
            continue
        conn.execute("DELETE FROM docs WHERE path=?", (rel,))
        if kind == "__log__":
            chunks = _log_chunks(path)
            warnings += log_problems(parse_log(path))
        else:
            chunks, meta = chunk_page(path, kind)
            if kind == "decision" and not _status(meta):
                review_by = meta.get("review_by")
                if not review_by:
                    warnings.append(f"decision without review_by: {path.name}")
                elif review_by < today:
                    warnings.append(f"decision past review_by={review_by}: {path.name}")
        for c in chunks:
            conn.execute(
                "INSERT OR REPLACE INTO docs(ref,path,kind,project,title,date,freshness,body) "
                "VALUES (:ref,:path,:kind,:project,:title,:date,:freshness,:body)", c)
        conn.execute("INSERT OR REPLACE INTO files(path,mtime,content_hash) VALUES (?,?,?)",
                     (rel, mtime, h))
        stats["indexed"] += 1
        stats["chunks"] += len(chunks)

    for (p,) in conn.execute("SELECT path FROM files").fetchall():
        if p not in seen:
            conn.execute("DELETE FROM docs WHERE path=?", (p,))
            conn.execute("DELETE FROM files WHERE path=?", (p,))
    conn.commit()
    conn.close()
    stats["warnings"] = warnings
    return stats


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w-]+", query.lower())
    return " OR ".join(f'"{t}"' for t in terms) if terms else '""'


def recall(query: str, project: str | None = None, limit: int = 8,
           include_historic: bool = False) -> list[dict]:
    """Full-text search over pages and log entries, ranked by bm25 and then
    re-ordered so current knowledge comes before historic/superseded."""
    refresh_index()
    sql = ("SELECT d.ref, d.path, d.kind, d.project, d.title, d.date, d.freshness, "
           "snippet(docs_fts, 1, '', '', '…', 12) "
           "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid WHERE docs_fts MATCH ?")
    params: list = [_fts_query(query)]
    if project:
        sql += " AND d.project = ?"
        params.append(norm_project(project))
    if not include_historic:
        sql += " AND d.freshness != 'historic'"
    sql += " ORDER BY bm25(docs_fts) LIMIT ?"
    params.append(limit * 3)
    conn = _connect()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    rows.sort(key=lambda r: FRESHNESS_ORDER.get(r[6], 1))
    return [{"ref": ref, "path": path, "kind": kind, "project": proj, "title": title,
             "date": date, "freshness": fr, "extract": snippet}
            for ref, path, kind, proj, title, date, fr, snippet in rows[:limit]]


# --- brief (the SessionStart payload) ----------------------------------------

def _catalog() -> str:
    lines = []
    for sub, label in (("projects", "Projects"), ("decisions", "Decisions")):
        names = sorted(p.stem for p in (wiki_dir() / sub).glob("*.md") if not _is_history(p.stem))
        if names:
            lines.append(f"{label}: " + ", ".join(names))
    return "\n".join(lines)


def truncate(text: str, cap: int, ref_hint: str) -> str:
    if len(text) <= cap:
        return text
    out, total = [], 0
    for line in text.splitlines():
        if total + len(line) > cap:
            break
        out.append(line)
        total += len(line)
    out.append(f"…(truncated, see {ref_hint})")
    return "\n".join(out)


def brief(cwd_or_project: str | None, budget: int = 1200) -> dict:
    """Cheap, cwd-aware orientation (~budget chars): catalog + the project's
    stable context + its latest log entries. Used by the hook and the MCP tool."""
    project = resolve_project(cwd_or_project) if cwd_or_project else None
    w = wiki_dir()
    blocks = [
        "## Memory\n"
        "Tools: memory_recall(query), memory_page(name, section), memory_save(...), "
        "memory_checkpoint(...), memory_lesson(...).",
        "### Index\n" + truncate(_catalog() or "(empty wiki)", budget * 7 // 12, "memory_page"),
    ]
    entries = parse_log(w / "log.md")
    probs = log_problems(entries)
    valid = [e for e in entries if not e.get("malformed")]

    if project:
        chunks, _ = chunk_page(w / "projects" / f"{project}.md", "project")
        top = next((c for c in chunks if c["ref"].endswith("#top")), None)
        if top and top["body"]:
            blocks.append(f"### {project} — context\n" + truncate(top["body"], budget * 3 // 12, top["ref"]))
        recent = [e for e in valid if e["project"] == project][-CURRENT_LOG_ENTRIES_PER_PROJECT:]
        if recent:
            lines = []
            for i, e in enumerate(reversed(recent)):
                lines.append(f"- [{e['date']}] {e['action']}")
                lines += [f"  - {b.lstrip('- ')}" for b in e["bullets"]
                          if i == 0 or b.lstrip("- ").lower().startswith(("todo", "pending", "pendiente"))]
            blocks.append(f"### {project} — latest sessions\n"
                          + truncate("\n".join(lines), budget * 6 // 12, "wiki/log.md"))
        else:
            blocks.append(f"### {project} — latest sessions\n(no log entries yet)")
    else:
        recent = valid[-CURRENT_LOG_ENTRIES_PER_PROJECT:]
        if recent:
            blocks.append("### Recent activity (all projects)\n" + "\n".join(
                f"- [{e['date']}] {e['project_raw']}: {e['action']}" for e in reversed(recent)))
        if cwd_or_project:
            blocks.append(f"No project page matches '{Path(cwd_or_project).name}'. "
                          "memory_save(project=...) creates one.")
    if project:
        # Imported here: drift/lessons depend on core, and brief() is the only caller.
        from memory_boost import drift, lessons
        if lines := drift.for_project(project):
            blocks.append(f"### {project} — drift\n" + "\n".join(lines))
        if found := lessons.lessons_for(project):
            blocks.append("### Lessons from other projects\n" + "\n".join(lessons.format_lessons(found)))
    if probs:
        blocks.append(f"### log.md — {len(probs)} unparseable headers\n" + "\n".join(probs))
    return {"text": "\n\n".join(blocks), "project": project}


# --- writes ------------------------------------------------------------------

def save_event(agent: str, project: str, action: str, result: str = "ok",
               detail: str | None = None) -> Path:
    """Append one raw JSONL event under events/<agent>/<day>.jsonl. Any agent
    that can append a line (a remote bot, a CI job) can feed the memory."""
    d = events_dir() / safe_slug(agent, "agent")
    d.mkdir(parents=True, exist_ok=True)
    event = {"ts": _now_iso(), "agent": agent, "project": project, "action": action,
             "result": result}
    if detail:
        event["detail"] = detail
    path = d / f"{_today()}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def ensure_project_page(project: str) -> Path:
    slug = safe_slug(project, "project")
    p = wiki_dir() / "projects" / f"{slug}.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntitle: {slug}\ncreated: {_today()}\n---\n# {slug}\n\n"
                     "(Stable context: what this project is, where it lives, how to run it.)\n",
                     encoding="utf-8")
    return p


def append_log(project: str, agent: str, action: str, bullets: list[str] | None = None,
               pending: str | None = None) -> None:
    """Append a compiled entry to wiki/log.md. Every field is collapsed to one
    line so user/LLM text can never forge an extra '## [date]' header."""
    agent, project = safe_slug(agent, "agent"), safe_slug(project, "project")
    header = f"## [{_today()}] {agent} | {project} | {_one_line(action)}"
    lines = [header] + [f"- {_one_line(b)}" for b in (bullets or []) if b.strip()]
    if pending:
        lines.append(f"- Pending: {_one_line(pending)}")
    log = wiki_dir() / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines) + "\n")


def save(project: str, action: str, detail: str | None = None, result: str = "ok",
         agent: str = "mcp", pending: str | None = None) -> str:
    if not action.strip():
        raise ValueError("action is empty")
    slug = safe_slug(project, "project")
    ensure_project_page(slug)
    save_event(agent, slug, action, result, detail)
    append_log(slug, agent, action, bullets=[detail] if detail else None, pending=pending)
    return slug


def page_path(name: str) -> tuple[str, Path] | None:
    """(kind, path) of the wiki page called `name`, or None if no section has it."""
    slug = safe_slug(name, "page name")
    for kind, sub in PAGE_DIRS:
        p = wiki_dir() / sub / f"{slug}.md"
        if p.exists():
            return kind, p
    return None


def get_page(name: str, section: str | None = None) -> str:
    found = page_path(name)
    if not found:
        return f"(page '{safe_slug(name, 'page name')}' not found)"
    kind, p = found
    chunks, _ = chunk_page(p, kind)
    if not section:
        return "\n\n".join(f"## {c['title']}\n{c['body']}" for c in chunks)
    for c in chunks:
        if slugify(c["title"]) == slugify(section) or c["ref"].endswith(f"#{section}"):
            return c["body"]
    return (f"(section '{section}' not found in {p.stem}; available: "
            f"{', '.join(c['title'] for c in chunks)})")


# --- checkpoints ---------------------------------------------------------------
#
# Live state of ONE working session, meant to survive context compaction and to
# let parallel sessions on the same project see each other. Deliberately cheap:
# one small JSON per session, no index involved. Ephemeral: at the end of the
# session it is distilled into log.md with save() and marked status=done.

CHECKPOINT_STALE_HOURS = 72
CHECKPOINT_DONE_CAP = 40
CHECKPOINT_STATUSES = ("active", "blocked", "done")


def _ckpt_path(project: str, session_id: str) -> Path:
    sid = slugify(session_id)
    if not sid:
        raise ValueError(f"invalid session_id: {session_id!r}")
    return checkpoints_dir() / safe_slug(project, "project") / f"{sid}.json"


def save_checkpoint(project: str, session_id: str, task: str, *, agent: str = "agent",
                    done: list[str] | None = None, next_step: str | None = None,
                    files: list[str] | None = None, notes: list[str] | None = None,
                    status: str = "active", plan_ref: str | None = None,
                    replace_done: bool = False) -> dict:
    """Merge-write a session checkpoint. `done` accumulates unless replace_done;
    other fields overwrite when given."""
    if status not in CHECKPOINT_STATUSES:
        raise ValueError(f"invalid status: {status!r} ({'|'.join(CHECKPOINT_STATUSES)})")
    if not task.strip():
        raise ValueError("task is empty")
    path = _ckpt_path(project, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev: dict = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"corrupt checkpoint {path}: {e}") from e
    merged_done = [] if replace_done else list(prev.get("done", []))
    merged_done += [d for d in (done or []) if d and d not in merged_done]
    ckpt = {
        "project": norm_project(project),
        "session_id": session_id,
        "agent": agent,
        "created": prev.get("created", _now_iso()),
        "updated": _now_iso(),
        "status": status,
        "task": task.strip(),
        "done": merged_done[-CHECKPOINT_DONE_CAP:],
        "next": (next_step if next_step is not None else prev.get("next")) or "",
        "files": sorted(set(prev.get("files", [])) | set(files or [])),
        "notes": (notes if notes is not None else prev.get("notes")) or [],
        "plan_ref": plan_ref if plan_ref is not None else prev.get("plan_ref"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ckpt, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)  # atomic: a parallel session never reads half a file
    return ckpt


def load_checkpoint(project: str, session_id: str) -> dict | None:
    path = _ckpt_path(project, session_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _is_stale(ckpt: dict) -> bool:
    try:
        upd = dt.datetime.strptime(ckpt["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    except (KeyError, ValueError):
        return True
    return (dt.datetime.now(dt.UTC) - upd).total_seconds() / 3600 > CHECKPOINT_STALE_HOURS


def list_checkpoints(project: str | None = None, include_done: bool = False) -> list[dict]:
    """Active (not done, not stale) checkpoints, newest first."""
    root = checkpoints_dir()
    dirs = [root / safe_slug(project, "project")] if project else (
        [p for p in root.iterdir() if p.is_dir()] if root.exists() else [])
    out = []
    for d in dirs:
        for f in d.glob("*.json") if d.exists() else ():
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if include_done or (c.get("status") != "done" and not _is_stale(c)):
                out.append(c)
    out.sort(key=lambda c: c.get("updated", ""), reverse=True)
    return out


def format_other_sessions(others: list[dict]) -> list[str]:
    return [f"- [{c['agent']} · {c['updated'][:16]}] {c['task']} → next: {c.get('next') or '?'}"
            + (f" · files: {', '.join(c['files'][:5])}" if c.get("files") else "")
            for c in others[:6]]


def resume_text(project: str, session_id: str | None = None, budget: int = 1200) -> str:
    """Compact text to re-inject after compaction: this session's checkpoint in
    full + one line per other active session on the project."""
    lines = [f"## Session checkpoint — {norm_project(project)}"]
    own = load_checkpoint(project, session_id) if session_id else None
    if own:
        lines.append(f"session_id: {session_id} · status: {own['status']} · updated: {own['updated']}")
        lines.append(f"**Task:** {own['task']}")
        if own.get("plan_ref"):
            lines.append(f"Plan: {own['plan_ref']}")
        if own["done"]:
            lines.append("Done:")
            lines += [f"- {d}" for d in own["done"][-12:]]
        if own.get("next"):
            lines.append(f"**Next:** {own['next']}")
        if own.get("files"):
            lines.append("Files: " + ", ".join(own["files"][:20]))
        lines += [f"- note: {n}" for n in own.get("notes", [])[:6]]
    elif session_id:
        lines.append(f"(no checkpoint for session_id={session_id}; create one with memory_checkpoint)")
    others = [c for c in list_checkpoints(project) if c["session_id"] != session_id]
    if others:
        lines.append("Other active sessions on this project (don't step on their scope):")
        lines += format_other_sessions(others)
    return truncate("\n".join(lines), budget, "memory_resume")
