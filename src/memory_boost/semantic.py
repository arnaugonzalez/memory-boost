"""Optional semantic recall: EmbeddingGemma through a local Ollama, vectors in sqlite-vec.

Opt-in: MEMORY_BOOST_SEMANTIC=1 plus the `semantic` extra. Stdlib HTTP to Ollama; sqlite-vec is
imported lazily so the default install never touches it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import urllib.error
import urllib.request

OLLAMA_URL = os.environ.get("MEMORY_BOOST_OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("MEMORY_BOOST_EMBED_MODEL", "embeddinggemma")
DIMS = 768
QUERY_PREFIX = "task: search result | query: "
DOC_PREFIX = "title: none | text: "
DOC_CHARS = 8000
BATCH = 16
TIMEOUT = 60


class SemanticError(RuntimeError):
    """Semantic recall is off, Ollama is unreachable, or the model is not pulled."""


def enabled() -> bool:
    if os.environ.get("MEMORY_BOOST_SEMANTIC") != "1":
        return False
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    return True


def _l2(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _post(texts: list[str]) -> list[list[float]]:
    if not OLLAMA_URL.startswith(("http://", "https://")):
        raise SemanticError(f"MEMORY_BOOST_OLLAMA_URL must be http(s)://, got {OLLAMA_URL!r}")
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        # The scheme is checked above, so urlopen cannot be steered to file:// or custom schemes.
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # nosec B310
            vecs = json.loads(resp.read())["embeddings"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SemanticError(f"model '{MODEL}' not available: ollama pull {MODEL}") from e
        raise SemanticError(f"ollama answered {e.code} at {OLLAMA_URL}") from e
    except (OSError, ValueError, KeyError) as e:
        raise SemanticError(f"ollama not reachable at {OLLAMA_URL}") from e
    if len(vecs) != len(texts) or any(len(v) != DIMS for v in vecs):
        raise SemanticError(f"ollama returned unexpected embeddings (want {len(texts)} x {DIMS})")
    return vecs


def embed(texts: list[str]) -> list[list[float]]:
    """Embed already-prefixed texts; returns L2-normalised vectors in batches of BATCH."""
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        out.extend(_l2(v) for v in _post(texts[i:i + BATCH]))
    return out


def ensure_schema(conn: sqlite3.Connection) -> None:
    if not enabled():
        raise SemanticError("semantic recall is off: set MEMORY_BOOST_SEMANTIC=1 (needs the semantic extra)")
    import sqlite_vec
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.OperationalError) as e:
        raise SemanticError(f"this Python's sqlite3 cannot load extensions: {e}") from e
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_docs USING vec0("
                 f"doc_id INTEGER PRIMARY KEY, embedding float[{DIMS}])")
    # Hash of the embedded text per doc: sqlite reuses rowids, so an id alone can't prove freshness.
    conn.execute("CREATE TABLE IF NOT EXISTS vec_src(doc_id INTEGER PRIMARY KEY, h TEXT)")


def _doc_text(title: str | None, body: str | None) -> str:
    return (DOC_PREFIX + (title or "") + "\n" + (body or ""))[:DOC_CHARS]


def _store(conn: sqlite3.Connection, rows: list[tuple[int, bytes, str]]) -> None:
    conn.executemany("INSERT INTO vec_docs(doc_id, embedding) VALUES (?, ?)", [(j, v) for j, v, _ in rows])
    conn.executemany("INSERT INTO vec_src(doc_id, h) VALUES (?, ?)", [(j, h) for j, _, h in rows])
    conn.commit()


def sync(conn: sqlite3.Connection) -> int:
    """Embed docs that are new or changed, drop vectors of docs that are gone. Returns embedded count."""
    import sqlite_vec
    ensure_schema(conn)
    texts = {i: _doc_text(t, b) for i, t, b in conn.execute("SELECT id, title, body FROM docs")}
    hashes = {i: hashlib.sha1(s.encode(), usedforsecurity=False).hexdigest() for i, s in texts.items()}
    have = dict(conn.execute("SELECT doc_id, h FROM vec_src").fetchall())
    todo = [i for i in hashes if have.get(i) != hashes[i]]
    # A changed file is deleted and re-chunked, so its unchanged chunks come back under new ids:
    # reuse the stored vector of the same content hash instead of embedding it again. Read before deleting.
    old_id_of = {h: d for d, h in have.items()}
    carried: list[tuple[int, bytes, str]] = []
    fresh: list[int] = []
    for j in todo:
        row = None
        if (old := old_id_of.get(hashes[j])) is not None:
            row = conn.execute("SELECT embedding FROM vec_docs WHERE doc_id = ?", (old,)).fetchone()
        if row:
            carried.append((j, row[0], hashes[j]))
        else:
            fresh.append(j)
    stale = [(i,) for i in have if i not in hashes] + [(i,) for i in todo]
    conn.executemany("DELETE FROM vec_docs WHERE doc_id = ?", stale)
    conn.executemany("DELETE FROM vec_src WHERE doc_id = ?", stale)
    conn.commit()
    _store(conn, carried)
    for i in range(0, len(fresh), BATCH):
        batch = fresh[i:i + BATCH]
        vecs = embed([texts[j] for j in batch])
        rows = [(j, sqlite_vec.serialize_float32(v), hashes[j]) for j, v in zip(batch, vecs, strict=True)]
        _store(conn, rows)
    return len(fresh)


def search(conn: sqlite3.Connection, query: str, k: int) -> list[tuple[int, float]]:
    """Nearest docs to the query as (docs.id, distance), closest first."""
    ensure_schema(conn)
    import sqlite_vec
    [qv] = embed([QUERY_PREFIX + query])
    return conn.execute("SELECT doc_id, distance FROM vec_docs WHERE embedding MATCH ? AND k = ?",
                        (sqlite_vec.serialize_float32(qv), k)).fetchall()
