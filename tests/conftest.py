import shutil
from importlib import resources

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated MEMORY_BOOST_HOME seeded with the example wiki; index in tmp too."""
    h = tmp_path / "home"
    src = resources.files("memory_boost") / "example_wiki"
    with resources.as_file(src) as p:
        shutil.copytree(p, h / "wiki")
    monkeypatch.setenv("MEMORY_BOOST_HOME", str(h))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEMORY_BOOST_TODAY", "2026-09-24")  # the example wiki ages; tests must not
    for var in ("MEMORY_BOOST_WIKI", "MEMORY_BOOST_INDEX", "MEMORY_BOOST_CHECKPOINTS"):
        monkeypatch.delenv(var, raising=False)
    return h
