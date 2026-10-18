"""Semantic recall without a real Ollama: embed() is replaced by a deterministic concept embedder."""
import logging
import re
import sys
import zlib

import pytest

pytest.importorskip("sqlite_vec")

from memory_boost import core, semantic  # noqa: E402

_REAL_EMBED = semantic.embed
# Synonym groups share one dimension, so "rotate credentials" lands near "renew ... tokens".
CONCEPTS = {"renew": 0, "rotate": 0, "rotation": 0, "change": 0,
            "tokens": 1, "credentials": 1, "secrets": 1}
PREFIX_WORDS = {"task", "search", "result", "query", "title", "none", "text"}


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * semantic.DIMS
        for w in re.findall(r"\w+", t.lower()):
            if w in PREFIX_WORDS:
                continue
            v[CONCEPTS.get(w, 2 + zlib.crc32(w.encode()) % (semantic.DIMS - 2))] += 1.0
        out.append(semantic._l2(v))
    return out


def stems(results):
    return [r["path"].rsplit("/", 1)[-1].removesuffix(".md") for r in results]


@pytest.fixture
def semantic_home(home, monkeypatch):
    monkeypatch.setenv("MEMORY_BOOST_SEMANTIC", "1")
    monkeypatch.delenv("MEMORY_BOOST_RECALL_MODE", raising=False)
    monkeypatch.setattr(semantic, "embed", fake_embed)
    (home / "wiki/decisions/renew-api-tokens.md").write_text(
        "# Renew API tokens\n\nWe renew the API tokens every quarter, no manual steps.\n", encoding="utf-8")
    return home


def test_sync_is_incremental(semantic_home):
    assert core.refresh_index()["embedded"] > 0
    assert core.refresh_index()["embedded"] == 0


def test_rechunked_unchanged_docs_keep_their_vectors(semantic_home):
    core.refresh_index()
    conn = core._connect()
    try:
        semantic.ensure_schema(conn)
        conn.execute("UPDATE docs SET id = id + 1000")  # what a re-chunked file looks like to sync
        conn.commit()
        assert semantic.sync(conn) == 0
        assert conn.execute("SELECT COUNT(*) FROM vec_docs").fetchone()[0] == \
            conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        [(doc_id, _)] = semantic.search(conn, "rotate credentials", 1)
        assert conn.execute("SELECT path FROM docs WHERE id = ?", (doc_id,)).fetchone()[0].endswith(
            "renew-api-tokens.md")
    finally:
        conn.close()


def test_deleting_a_page_deletes_its_vector(semantic_home):
    core.refresh_index()
    (semantic_home / "wiki/decisions/renew-api-tokens.md").unlink()
    core.refresh_index()
    conn = core._connect()
    try:
        semantic.ensure_schema(conn)
        orphans = conn.execute("SELECT COUNT(*) FROM vec_docs WHERE doc_id NOT IN (SELECT id FROM docs)")
        assert orphans.fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM vec_docs").fetchone()[0] == \
            conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    finally:
        conn.close()


def test_edited_page_is_reembedded(semantic_home):
    core.refresh_index()
    (semantic_home / "wiki/decisions/renew-api-tokens.md").write_text(
        "# Rotate secrets\n\nWe rotate the secrets monthly.\n", encoding="utf-8")
    assert core.refresh_index()["embedded"] >= 1
    assert "renew-api-tokens" in stems(core.recall("renew tokens", mode="semantic"))


def test_synonym_query_finds_page_fts_misses(semantic_home):
    query = "rotate credentials"
    assert "renew-api-tokens" not in stems(core.recall(query, mode="fts"))
    assert "renew-api-tokens" in stems(core.recall(query, mode="semantic")[:3])
    assert "renew-api-tokens" in stems(core.recall(query, mode="hybrid")[:3])


def test_ollama_down_falls_back_to_fts(semantic_home, monkeypatch, caplog):
    monkeypatch.setattr(semantic, "embed", _REAL_EMBED)
    monkeypatch.setattr(semantic, "OLLAMA_URL", "http://127.0.0.1:9")
    with caplog.at_level(logging.WARNING):
        res = core.recall("rotate credentials", mode="semantic")
    assert res == core.recall("rotate credentials", mode="fts")
    assert "semantic recall unavailable" in caplog.text


def test_disabled_never_imports_sqlite_vec(home, monkeypatch):
    monkeypatch.delenv("MEMORY_BOOST_SEMANTIC", raising=False)
    monkeypatch.delenv("MEMORY_BOOST_RECALL_MODE", raising=False)
    monkeypatch.delitem(sys.modules, "sqlite_vec", raising=False)
    assert core.recall("SKIP LOCKED")
    assert "sqlite_vec" not in sys.modules


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://localhost", "localhost:11434"])
def test_ollama_url_must_be_http(monkeypatch, url):
    monkeypatch.setattr(semantic, "OLLAMA_URL", url)
    with pytest.raises(semantic.SemanticError, match="must be http"):
        _REAL_EMBED(["x"])
