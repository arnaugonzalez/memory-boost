"""Core logic: FTS5 index over a markdown wiki, freshness ("vigencia") rules,
the cheap session brief, event/log writes and session checkpoints.

Shared by the CLI (hooks) and the MCP server so that "cheap recall at session
start" and "recall on demand" never drift apart. Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_boost import semantic

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
# bm25() is negative (more negative = better): a weight < 1 pulls the score toward 0 and drops the chunk.
FRESHNESS_WEIGHT = {"current": 1.0, "unclassified": 1.0, "review": 0.85, "historic": 0.5, "superseded": 0.3}
# Frontmatter `status:` values accepted as aliases (wikis written in Spanish).
STATUS_ALIASES = {"vigente": "current", "revisar": "review", "historico": "historic",
                  "histórico": "historic", "accepted": "current", "active": "current",
                  "proposed": "review"}

# How many of a project's most recent log entries count as "current": used
# both to classify log chunks and to cap the recent activity in brief().
CURRENT_LOG_ENTRIES_PER_PROJECT = 3
# Open pending items shown per project in brief() (the list is already severity-sorted).
BRIEF_PENDING_ITEMS = 8
# Log bullets that carry v2 pending ids ("[proj#n|sN]"): brief() takes those from the
# open-pending fold instead, so a closed item does not reappear through the log.
_PENDING_TAG_RE = re.compile(r"\[[a-z0-9-]+#\d+\|")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def slugify(name: str) -> str:
    """'Validar código' -> 'validar-codigo': accents are folded, not dropped with the letter."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", name.lower())
                     if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


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
    if kind == "decision" and (status := _status(meta)):
        return status
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
    if semantic.enabled():
        try:
            stats["embedded"] = semantic.sync(conn)
        except semantic.SemanticError as e:
            warnings.append(f"semantic index skipped: {e}")
    conn.close()
    stats["warnings"] = warnings
    return stats


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w-]+", query.lower())
    return " OR ".join(f'"{t}"' for t in terms) if terms else '""'


RECALL_MODES = ("fts", "semantic", "hybrid")
RRF_K = 60


def _hit(ref, path, kind, proj, title, date, fr, extract) -> dict:
    return {"ref": ref, "path": path, "kind": kind, "project": proj, "title": title,
            "date": date, "freshness": fr, "extract": extract}


def _fts_hits(conn: sqlite3.Connection, query: str, project: str | None,
              include_historic: bool, n: int) -> list[dict]:
    sql = ("SELECT d.ref, d.path, d.kind, d.project, d.title, d.date, d.freshness, "
           "snippet(docs_fts, 1, '', '', '…', 12), bm25(docs_fts) "
           "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid WHERE docs_fts MATCH ?")
    params: list = [_fts_query(query)]
    if project:
        sql += " AND d.project = ?"
        params.append(norm_project(project))
    if not include_historic:
        sql += " AND d.freshness != 'historic'"
    sql += " ORDER BY bm25(docs_fts) LIMIT ?"
    params.append(n)
    rows = conn.execute(sql, params).fetchall()
    rows.sort(key=lambda r: r[8] * FRESHNESS_WEIGHT.get(r[6], 1.0))
    return [_hit(ref, path, kind, proj, title, date, fr, snippet)
            for ref, path, kind, proj, title, date, fr, snippet, _bm25 in rows]


def _semantic_hits(conn: sqlite3.Connection, query: str, project: str | None,
                   include_historic: bool, n: int) -> list[dict]:
    hits = []
    for doc_id, dist in semantic.search(conn, query, n):
        ref, path, kind, proj, title, date, fr, body = conn.execute(
            "SELECT ref, path, kind, project, title, date, freshness, body FROM docs WHERE id = ?",
            (doc_id,)).fetchone()
        if project and proj != norm_project(project):
            continue
        if not include_historic and fr == "historic":
            continue
        extract = " ".join((body or "").split())[:160]
        weight = FRESHNESS_WEIGHT.get(fr, 1.0)
        hits.append((dist / weight, _hit(ref, path, kind, proj, title, date, fr, extract)))
    hits.sort(key=lambda h: h[0])
    return [h for _, h in hits]


def _hybrid_hits(conn: sqlite3.Connection, query: str, project: str | None,
                 include_historic: bool, n: int) -> list[dict]:
    """Weighted reciprocal-rank fusion: semantic counts double, bm25 once."""
    fts = _fts_hits(conn, query, project, include_historic, n)
    vec = _semantic_hits(conn, query, project, include_historic, n)
    fused: dict[str, float] = {}
    first: dict[str, dict] = {}
    for weight, ranked in ((1.0, fts), (2.0, vec)):
        for rank, h in enumerate(ranked, 1):
            first.setdefault(h["ref"], h)
            fused[h["ref"]] = fused.get(h["ref"], 0.0) + weight / (RRF_K + rank)
    return [first[ref] for ref in sorted(fused, key=lambda r: -fused[r])]


def recall(query: str, project: str | None = None, limit: int = 8,
           include_historic: bool = False, mode: str | None = None) -> list[dict]:
    """Search pages and log entries, with stale knowledge down-weighted.

    mode: "fts" (bm25), "semantic" (EmbeddingGemma vectors) or "hybrid" (RRF of both). Default:
    MEMORY_BOOST_RECALL_MODE, else "hybrid" when semantic.enabled(), else "fts". Semantic modes
    fall back to "fts" with a logged warning when Ollama or sqlite-vec is unavailable.
    """
    refresh_index()
    mode = mode or os.environ.get("MEMORY_BOOST_RECALL_MODE") or ("hybrid" if semantic.enabled() else "fts")
    if mode not in RECALL_MODES:
        raise ValueError(f"unknown recall mode {mode!r}; use one of {', '.join(RECALL_MODES)}")
    conn = _connect()
    try:
        if mode == "fts":
            hits = _fts_hits(conn, query, project, include_historic, limit * 3)
        else:
            search = _semantic_hits if mode == "semantic" else _hybrid_hits
            try:
                hits = search(conn, query, project, include_historic, limit * 3)
            except semantic.SemanticError as e:
                logging.warning("semantic recall unavailable, using fts: %s", e)
                hits = _fts_hits(conn, query, project, include_historic, limit * 3)
    finally:
        conn.close()
    return hits[:limit]


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
            room = cap - total
            if room > 40:  # a long line (e.g. the project catalog) is cut, not dropped
                out.append(line[:room].rsplit(" ", 1)[0] + " …")
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
    pending_open: list[dict] = []

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
                          if not _PENDING_TAG_RE.search(b)
                          and (i == 0 or b.lstrip("- ").lower().startswith(("todo", "pending", "pendiente")))]
            blocks.append(f"### {project} — latest sessions\n"
                          + truncate("\n".join(lines), budget * 6 // 12, "wiki/log.md"))
        else:
            blocks.append(f"### {project} — latest sessions\n(no log entries yet)")
        pending_open = open_pending(project)[:BRIEF_PENDING_ITEMS]
        if pending_open:
            lines = []
            for p in pending_open:
                line = f"- [{p['id']}|s{p['severity'] or 3}] {_one_line(p['text'] or '')}"
                if p.get("owner") and p["owner"] != "user":
                    line += f" (owner: {p['owner']})"
                if p.get("waiting_on"):
                    line += f" waiting_on: {p['waiting_on']}"
                if p.get("due"):
                    line += f" due: {p['due']}"
                if p["stale"]:
                    line += f" — still open? (>{PENDING_STALE_DAYS}d)"
                lines.append(line)
            blocks.append(f"### {project} — open pending\n"
                          + truncate("\n".join(lines), budget * 3 // 12, "memory-boost pending")
                          + "\nCite these ids in memory_save(closes=[...]) when done.")
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
    return {"text": "\n\n".join(blocks), "project": project, "pending_open": pending_open}


# --- writes ------------------------------------------------------------------

# --- session schema v2 (event schema v2) ----------------------
#
# A save is one event in events/<agent>/<day>.jsonl. v2 events carry `schema`, the
# pending items with server-assigned ids (<project>#<n>) and optional session and
# metrics blocks. Open pending items are a fold over the events: created, minus
# closed (`closes`), minus dropped (`drops`). Nothing is edited in place.

SCHEMA_V2 = "memory-boost/session@2"
OUTCOMES = ("done", "progress", "blocked", "waiting", "research", "abandoned")
PENDING_OWNERS = ("user", "agent", "client", "third_party")
PENDING_SEVERITIES = (1, 2, 3)
PENDING_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*#\d+$")
PENDING_STALE_DAYS = 28
SESSION_HARNESSES = ("claude-code", "codex", "hermes", "other")
MONEY_KINDS = ("quote", "invoice", "payment", "cost", "price")
MONEY_STATUSES = ("draft", "sent", "accepted", "paid", "rejected")
STATED_KINDS = ("energy", "mood", "worry", "load")
FRICTION_KINDS = ("time", "guess", "workaround")


def tz() -> ZoneInfo:
    """Zone of the local day in `date_local` (MEMORY_BOOST_TZ; UTC by default)."""
    return ZoneInfo(os.environ.get("MEMORY_BOOST_TZ") or "UTC")


def _parse_ts(ts_iso: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return t.replace(tzinfo=dt.UTC) if t.tzinfo is None else t


def date_local(ts_iso: str) -> str:
    """YYYY-MM-DD of a UTC ISO timestamp, in tz()."""
    return _parse_ts(ts_iso).astimezone(tz()).strftime("%Y-%m-%d")


def parse_pending(pending) -> list[dict]:
    """Normalise `pending` into [{text, owner, severity[, due][, waiting_on]}].
    Accepts None, the v1 string "a; b" (each part is the user's, severity 3) or a list
    of PendingIn objects."""
    if pending is None or pending == "":
        return []
    if isinstance(pending, str):
        return [{"text": part.strip(), "owner": "user", "severity": 3}
                for part in pending.split(";") if part.strip()]
    if not isinstance(pending, list):
        raise ValueError(f"pending must be a string or a list of objects, not {type(pending).__name__}")
    items = []
    for i, raw in enumerate(pending):
        if not isinstance(raw, dict):
            raise ValueError(f"pending[{i}] must be an object, not {type(raw).__name__}")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"pending[{i}].text must be a non-empty string")
        owner = raw.get("owner", "user")
        if owner not in PENDING_OWNERS:
            raise ValueError(f"pending[{i}].owner {owner!r} is not one of {'|'.join(PENDING_OWNERS)}")
        severity = raw.get("severity", 3)
        if type(severity) is not int or severity not in PENDING_SEVERITIES:
            raise ValueError(f"pending[{i}].severity must be 1, 2 or 3, not {severity!r}")
        item = {"text": text.strip(), "owner": owner, "severity": severity}
        if raw.get("due") is not None:
            try:
                dt.date.fromisoformat(raw["due"])
            except (TypeError, ValueError) as e:
                raise ValueError(f"pending[{i}].due must be YYYY-MM-DD, not {raw['due']!r}") from e
            item["due"] = raw["due"]
        if raw.get("waiting_on") is not None:
            if not isinstance(raw["waiting_on"], str):
                raise ValueError(f"pending[{i}].waiting_on must be a string")
            item["waiting_on"] = raw["waiting_on"]
        if "done_check" in raw:
            check = raw["done_check"]
            if not isinstance(check, str) or not check.strip():
                raise ValueError(f"pending[{i}].done_check must be a non-empty string "
                                 "(a command or an observable condition)")
            item["done_check"] = check.strip()
        items.append(item)
    return items


def assign_pending_ids(project: str, items: list[dict]) -> list[dict]:
    """Give each item the next id `<project>#<n>`; the counter per project lives in
    events/pending-seq.json and is written atomically."""
    slug = safe_slug(project, "project")
    path = events_dir() / "pending-seq.json"
    seq: dict = {}
    if path.exists():
        try:
            seq = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"corrupt {path}: {e}") from e
    n = int(seq.get(slug, 0))
    out = []
    for item in items:
        n += 1
        out.append({**item, "id": f"{slug}#{n}"})
    if items:
        seq[slug] = n
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(seq, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)  # atomic, as shelf() does
    return out


def iter_events() -> list[dict]:
    """Every event of every agent, oldest first. Broken lines are skipped so one
    half-written line cannot blind the fold."""
    root = events_dir()
    if not root.exists():
        return []
    out = []
    for f in root.glob("*/*.jsonl"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    out.sort(key=lambda e: str(e.get("ts", "")))
    return out


def _session_id_of(ev: dict) -> str | None:
    sess = ev.get("session")
    return sess.get("id") if isinstance(sess, dict) else None


def session_seq(session_id: str) -> tuple[int, str | None]:
    """(number of this save in the session, ts of the previous save or None)."""
    prev = [e for e in iter_events() if _session_id_of(e) == session_id]
    return len(prev) + 1, (prev[-1].get("ts") if prev else None)


def open_pending(project: str | None = None) -> list[dict]:
    """Pending items not closed by `closes` nor dropped by `drops` (any project), most
    severe and oldest first. Also refreshes events/pending-open.json, which is only a
    convenience cache: it is never read back."""
    created: dict[str, dict] = {}
    done: set[str] = set()
    for ev in iter_events():
        pend = ev.get("pending") if isinstance(ev.get("pending"), list) else []
        for item in pend:
            if isinstance(item, dict) and item.get("id"):
                created.setdefault(item["id"], {
                    "id": item["id"], "text": item.get("text"), "owner": item.get("owner"),
                    "severity": item.get("severity"), "due": item.get("due"),
                    "waiting_on": item.get("waiting_on"), "done_check": item.get("done_check"),
                    "project": ev.get("project"),
                    "created": ev.get("ts")})
        closes = ev.get("closes") if isinstance(ev.get("closes"), list) else []
        done.update(cid for cid in closes if isinstance(cid, str))
        drops = ev.get("drops") if isinstance(ev.get("drops"), list) else []
        done.update(d["id"] for d in drops if isinstance(d, dict) and d.get("id"))
    today = dt.date.fromisoformat(_today())
    out = []
    for pid, c in created.items():
        if pid in done:
            continue
        age = (today - dt.date.fromisoformat(date_local(c["created"]))).days
        out.append({**c, "stale": age > PENDING_STALE_DAYS})
    out.sort(key=lambda p: (p["severity"] or 3, p["created"] or ""))

    cache = events_dir() / "pending-open.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(cache)
    if project is None:
        return out
    slug = norm_project(project)
    return [p for p in out if p["project"] == slug]


def _str_list(name: str, value, *, ids: bool = False):
    """None passes through; otherwise a list of non-empty strings (pending ids if `ids`)."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{name} must be a list of non-empty strings")
    if ids and not all(PENDING_ID_RE.match(v) for v in value):
        raise ValueError(f"{name} must hold pending ids like <project>#<n>, not {value!r}")
    return value


def _obj_list(name: str, value, required: dict[str, type]):
    """None passes through; otherwise a list of objects whose required keys have the given types."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ValueError(f"{name} must be a list of objects")
    for v in value:
        for key, typ in required.items():
            got = v.get(key)
            if not isinstance(got, typ) or (typ is str and not got.strip()):
                raise ValueError(f"{name}[].{key} must be a non-empty {typ.__name__}")
    return value


def _check_session(session):
    if session is None:
        return None
    if not isinstance(session, dict) or not isinstance(session.get("id"), str) or not session["id"].strip():
        raise ValueError("session must be an object with a non-empty string id")
    if session.get("harness") is not None and session["harness"] not in SESSION_HARNESSES:
        raise ValueError(f"invalid session.harness: {session['harness']!r} ({'|'.join(SESSION_HARNESSES)})")
    for key in ("segment_start", "segment_end"):
        if session.get(key) is not None:
            if not isinstance(session[key], str):
                raise ValueError(f"session.{key} must be an ISO timestamp")
            try:
                _parse_ts(session[key])
            except ValueError as e:
                raise ValueError(f"session.{key} must be an ISO timestamp, not {session[key]!r}") from e
    return dict(session)


def _check_money(money):
    money = _obj_list("money", money, {"kind": str, "status": str, "ref": str})
    for m in money or []:
        if m["kind"] not in MONEY_KINDS:
            raise ValueError(f"invalid money.kind: {m['kind']!r} ({'|'.join(MONEY_KINDS)})")
        if m["status"] not in MONEY_STATUSES:
            raise ValueError(f"invalid money.status: {m['status']!r} ({'|'.join(MONEY_STATUSES)})")
        amount = m.get("amount")
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, (int, float))):
            raise ValueError("money[].amount must be a number")
        if m.get("currency") is not None and m["currency"] not in ("EUR", "USD"):
            raise ValueError(f"invalid money.currency: {m['currency']!r} (EUR|USD)")
    return money


def _check_stated(stated):
    stated = _obj_list("stated", stated, {"what": str, "quote": str})
    for s in stated or []:
        if s["what"] not in STATED_KINDS:
            raise ValueError(f"invalid stated.what: {s['what']!r} ({'|'.join(STATED_KINDS)})")
    return stated


def _hhmm(ts_iso: str) -> str:
    return _parse_ts(ts_iso).astimezone(dt.UTC).strftime("%H:%M")


def _meta_line(event: dict) -> str | None:
    """The `- Meta:` line of a v2 entry. A part whose data is missing is left out."""
    sess = event.get("session")
    met = event.get("metrics")
    if not isinstance(sess, dict) and not isinstance(met, dict):
        return None
    parts = []
    if isinstance(sess, dict):
        parts.append(f"sid={str(sess['id'])[:8]}")
        if sess.get("segment_start") and sess.get("segment_end"):
            parts.append(f"seg={_hhmm(sess['segment_start'])}–{_hhmm(sess['segment_end'])}Z")
    if isinstance(met, dict):
        minutes = met.get("minutes")
        if isinstance(minutes, dict):
            shares = [f"{slug} {m['attention']}/{m['gross']}" for slug, m in minutes.items()
                      if isinstance(m, dict) and "attention" in m and "gross" in m]
            if shares:
                parts.append("min=" + ",".join(shares))
        commits = met.get("commits")
        if isinstance(commits, list):
            unpushed = sum(1 for c in commits if isinstance(c, dict) and not c.get("pushed"))
            parts.append(f"commits={len(commits)}({unpushed} unpushed)")
        tests = met.get("tests")
        if isinstance(tests, dict) and "passed" in tests and "failed" in tests:
            parts.append(f"tests={tests['passed']}ok/{tests['failed']}fail")
        if met.get("cost_usd") is not None:
            parts.append(f"cost={met['cost_usd']}$")
    if event.get("outcome"):
        parts.append(f"outcome={event['outcome']}")
    return "- Meta: " + " ".join(parts) if parts else None


def save_event(agent: str, project: str, action: str, result: str = "ok",
               detail: str | None = None, extra: dict | None = None) -> Path:
    """Append one raw JSONL event under events/<agent>/<day>.jsonl. Any agent
    that can append a line (a remote bot, a CI job) can feed the memory.
    `extra` is merged into the event (it may override ts and the v1 fields)."""
    d = events_dir() / safe_slug(agent, "agent")
    d.mkdir(parents=True, exist_ok=True)
    event = {"ts": _now_iso(), "agent": agent, "project": project, "action": action,
             "result": result}
    if detail:
        event["detail"] = detail
    if extra:
        event.update(extra)
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
               pending: str | None = None, *, event: dict | None = None) -> None:
    """Append a compiled entry to wiki/log.md. Every field is collapsed to one
    line so user/LLM text can never forge an extra '## [date]' header.

    With `event` (a v2 save) the header date is event["date_local"] (MEMORY_BOOST_TODAY
    still pins it) and the Meta, Pending, Closes and Drops lines come from the event;
    `pending` is then ignored. Without `event` the entry is the v1 one."""
    agent, project = safe_slug(agent, "agent"), safe_slug(project, "project")
    day = _today()
    if event is not None and not os.environ.get("MEMORY_BOOST_TODAY"):
        day = event.get("date_local") or day
    header = f"## [{day}] {agent} | {project} | {_one_line(action)}"
    lines = [header] + [f"- {_one_line(b)}" for b in (bullets or []) if b.strip()]
    if event is None:
        if pending:
            lines.append(f"- Pending: {_one_line(pending)}")
    else:
        if meta := _meta_line(event):
            lines.append(meta)
        if event.get("pending"):
            lines.append("- Pending: " + "; ".join(
                f"[{p['id']}|s{p['severity']}] {_one_line(p['text'])}" for p in event["pending"]))
        if event.get("closes"):
            lines.append("- Closes: " + ", ".join(event["closes"]))
        if event.get("drops"):
            lines.append("- Drops: " + "; ".join(
                f"{d['id']} ({_one_line(d['why'])})" for d in event["drops"]))
    log = wiki_dir() / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines) + "\n")


AUTO_MARK = "- Auto-session: "


def upsert_session_log(project: str, session_id: str, bullets: list[str]) -> None:
    """Write the single `auto` log.md entry of a session: replace it in place when it
    exists (found by its Auto-session line), append it otherwise. Only that entry's
    lines change, so entries written by hand or by save() are never touched."""
    project = safe_slug(project, "project")
    header = f"## [{_today()}] auto | {project} | session {session_id[:8]} (autosave)"
    block = [header, f"{AUTO_MARK}{_one_line(session_id)}"] + [
        f"- {_one_line(b)}" for b in bullets if b.strip()]
    log = wiki_dir() / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = log.read_text(encoding="utf-8").split("\n") if log.exists() else []
    mark = f"{AUTO_MARK}{_one_line(session_id)}"
    starts = [i for i, ln in enumerate(lines) if LOG_HEADER_LOOSE_RE.match(ln)]
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        if mark in lines[start:end]:
            while end > start and not lines[end - 1].strip():
                end -= 1
            lines[start:end] = block
            log.write_text("\n".join(lines), encoding="utf-8")
            return
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(block) + "\n")


def _only_open(closes, drops):
    """Keep the ids of `closes`/`drops` that are open right now; the rest is reported, not written,
    so a stale or mistyped id cannot mark nothing as done nor hide a real close."""
    if not closes and not drops:
        return closes, drops, []
    open_ids = {p["id"] for p in open_pending()}
    created = {item["id"] for ev in iter_events() if isinstance(ev.get("pending"), list)
               for item in ev["pending"] if isinstance(item, dict) and item.get("id")}
    seen: set[str] = set()
    ignored = []

    def keep(pid):
        if pid in seen:
            why = "repeated"
        elif pid in open_ids:
            why = None
        else:
            why = "already closed" if pid in created else "unknown"
        seen.add(pid)
        if why:
            ignored.append({"id": pid, "why": why})
        return why is None

    if closes is not None:
        closes = [c for c in closes if keep(c)]
    if drops is not None:
        drops = [d for d in drops if keep(d["id"])]
    return closes, drops, ignored


def _check_friction(friction):
    friction = _obj_list("friction", friction, {"what": str, "kind": str, "cause": str})
    for f in friction or []:
        if f["kind"] not in FRICTION_KINDS:
            raise ValueError(f"invalid friction.kind: {f['kind']!r} ({'|'.join(FRICTION_KINDS)})")
        f["cause"] = norm_project(f["cause"])
        if f.get("fix") is not None and not isinstance(f["fix"], str):
            raise ValueError("friction[].fix must be a string")
    return friction


def save_full(project: str, action: str, detail: str | None = None, result: str = "ok",
              agent: str = "mcp", pending: str | list | None = None, *,
              session: dict | None = None, metrics: dict | None = None,
              outcome: str | None = None, closes: list[str] | None = None,
              drops: list[dict] | None = None, decisions: list[dict] | None = None,
              deadlines: list[dict] | None = None, orgs: list[str] | None = None,
              touches: list[str] | None = None, uses: list[str] | None = None,
              billable: bool | None = None, money: list[dict] | None = None,
              stated: list[dict] | None = None, friction: list[dict] | None = None) -> dict:
    """Write one session event plus its log entry. Everything is validated before the
    first byte is written. The v2 fields are optional: a None field is left out."""
    if not action.strip():
        raise ValueError("action is empty")
    slug = safe_slug(project, "project")
    safe_slug(agent, "agent")  # the same rule save_event applies, checked before any write
    items = parse_pending(pending)
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r} ({'|'.join(OUTCOMES)})")
    closes = _str_list("closes", closes, ids=True)
    drops = _obj_list("drops", drops, {"id": str, "why": str})
    for d in drops or []:
        if not PENDING_ID_RE.match(d["id"]):
            raise ValueError(f"drops[].id must be a pending id like <project>#<n>, not {d['id']!r}")
    closes, drops, ignored = _only_open(closes, drops)
    session = _check_session(session)
    if metrics is not None and not isinstance(metrics, dict):
        raise ValueError("metrics must be an object")
    decisions = _obj_list("decisions", decisions, {"slug": str, "new": bool})
    deadlines = _obj_list("deadlines", deadlines, {"what": str, "at": str, "hard": bool})
    orgs = _str_list("orgs", orgs)
    touches = _str_list("touches", touches)
    uses = _str_list("uses", uses)
    if billable is not None and not isinstance(billable, bool):
        raise ValueError("billable must be true, false or null")
    money = _check_money(money)
    stated = _check_stated(stated)
    friction = _check_friction(friction)

    ensure_project_page(slug)
    ts = _now_iso()
    seq, prev_ts = session_seq(session["id"]) if session else (1, None)
    ids = assign_pending_ids(slug, items)
    event = {"ts": ts, "agent": agent, "project": slug, "action": action, "result": result}
    if detail:
        event["detail"] = detail
    event.update({"schema": SCHEMA_V2, "date_local": date_local(ts), "seq": seq, "pending": ids})
    if session is not None:
        event["session"] = {**session,
                            "segment_start": session.get("segment_start") or prev_ts or ts,
                            "segment_end": session.get("segment_end") or ts}
    optional = {"metrics": metrics, "outcome": outcome, "closes": closes, "drops": drops,
                "decisions": decisions, "deadlines": deadlines, "orgs": orgs,
                "touches": touches, "uses": uses, "billable": billable, "money": money,
                "stated": stated, "friction": friction}
    event.update({k: v for k, v in optional.items() if v is not None})
    save_event(agent, slug, action, result, detail, extra=event)
    append_log(slug, agent, action, bullets=[detail] if detail else None, event=event)
    return {"project": slug, "event": event, "pending_ids": [p["id"] for p in ids],
            "closes": closes or [], "ignored": ignored}


def save(project: str, action: str, detail: str | None = None, result: str = "ok",
         agent: str = "mcp", pending: str | None = None) -> str:
    return save_full(project, action, detail=detail, result=result, agent=agent,
                     pending=pending)["project"]


def save_summary(action: str, out: dict) -> str:
    """The one line that memory_save and `memory-boost save` print, from save_full's result:
    `saved: <project> | <action>`, then ` | pending: <ids>` and ` | closes: <ids>` if any."""
    line = f"saved: {out['project']} | {action}"
    if out["pending_ids"]:
        line += " | pending: " + ", ".join(out["pending_ids"])
    if out["closes"]:
        line += " | closes: " + ", ".join(out["closes"])
    if out.get("ignored"):
        line += " | ignored: " + ", ".join(f"{x['id']} ({x['why']})" for x in out["ignored"])
    return line


SHELF_DIRS = ("concepts", "projects", "decisions")
SHELF_TEXT_CAP = 500


def _set_updated(text: str, today: str) -> str:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return f"---\nupdated: {today}\n---\n{text}"
    head = m.group(1).splitlines()
    if any(ln.startswith("updated:") for ln in head):
        head = [f"updated: {today}" if ln.startswith("updated:") else ln for ln in head]
    else:
        head.append(f"updated: {today}")
    return "---\n" + "\n".join(head) + "\n---\n" + text[m.end():]


def shelf(text: str, page: str = "concepts/idea-shelf", project: str | None = None) -> dict:
    """Append a dated one-line idea to a wiki page without the caller reading the page."""
    rel_dir, _, name = page.removesuffix(".md").partition("/")
    if rel_dir not in SHELF_DIRS or not name:
        raise ValueError(f"shelf page must be <{'|'.join(SHELF_DIRS)}>/<name>: {page!r}")
    slug = safe_slug(name, "shelf page")
    idea = _one_line(text)[:SHELF_TEXT_CAP]
    if not idea:
        raise ValueError("idea is empty")
    today = _today()
    tag = f" ({norm_project(project)})" if project else ""
    line = f"- [{today}]{tag} {idea}"
    path = wiki_dir() / rel_dir / f"{slug}.md"
    if path.exists():
        body = _set_updated(path.read_text(encoding="utf-8"), today)
    else:
        body = (f"---\ntitle: {slug}\ncreated: {today}\nupdated: {today}\ntype: concept\n---\n"
                "# Idea shelf\n\n")
    if not body.endswith("\n"):
        body += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body + line + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic: a concurrent recall never reads half a page
    return {"page": f"{rel_dir}/{slug}.md", "line": line}


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
    if prev.get("auto"):
        ckpt["auto"] = prev["auto"]
    return write_checkpoint(project, session_id, ckpt)


def write_checkpoint(project: str, session_id: str, ckpt: dict) -> dict:
    path = _ckpt_path(project, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
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
        auto = own.get("auto")
        if auto:
            lines.append(f"Auto-captured ({auto['trigger']} · {auto['at'][:16]} · {auto['turns']} prompts):")
            if auto.get("request") and auto["request"] != own["task"]:
                lines.append(f"- first request: {auto['request']}")
            lines += [f"- recent prompt: {p}" for p in auto.get("recent_prompts", [])]
            if auto.get("commits"):
                lines.append("- commits: " + " | ".join(auto["commits"][-5:]))
    elif session_id:
        lines.append(f"(no checkpoint for session_id={session_id}; create one with memory_checkpoint)")
    others = [c for c in list_checkpoints(project) if c["session_id"] != session_id]
    if others:
        lines.append("Other active sessions on this project (don't step on their scope):")
        lines += format_other_sessions(others)
    return truncate("\n".join(lines), budget, "memory_resume")
