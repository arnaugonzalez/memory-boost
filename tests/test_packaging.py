"""The plugin, the marketplace entry and the Python package must describe the same release."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_plugin_version_matches_package():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert _json(".claude-plugin/plugin.json")["version"] == version


def test_marketplace_lists_this_plugin_from_the_repo_root():
    plugin = _json(".claude-plugin/plugin.json")
    [entry] = _json(".claude-plugin/marketplace.json")["plugins"]
    assert entry["name"] == plugin["name"] == "memory-boost" and entry["source"] == "./"


def test_plugin_launches_the_pinned_release_and_every_skill_exists():
    """The plugin directory blocks unpinned `uvx` launchers: run the exact release, not a checkout."""
    from memory_boost import __version__
    plugin = _json(".claude-plugin/plugin.json")
    server = plugin["mcpServers"]["memory"]
    assert server["command"] == "uvx"
    assert server["args"] == ["--quiet", f"memory-boost=={__version__}", "serve"]
    hook = plugin["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert hook == f"uvx --quiet memory-boost=={__version__} hook session-start"
    for skill in (ROOT / "skills").iterdir():
        assert (skill / "SKILL.md").is_file(), skill


# Every place that tells a launcher or an agent which memory-boost to install.
PINNED_FILES = [".claude-plugin/plugin.json", "server.json", "README.md", "llms-install.md"]


def test_every_pinned_install_matches_the_package_version():
    """A release bump that misses one of these files fails here, not in the plugin directory."""
    import re

    from memory_boost import __version__
    for rel in PINNED_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        pins = re.findall(r"memory-boost(?:\[[\w,]+\])?==([0-9][\w.]*)", text)
        assert all(v == __version__ for v in pins), (rel, pins)
        unpinned = re.findall(
            r"(?:uvx|uv tool install|pipx install|pipx run|pip install)(?: --?[\w-]+)* "
            r"[\"']?memory-boost(?:\[[\w,]+\])?(?![\[\w=-])[\"']?(?=[\s`]|$)", text, re.M)
        assert unpinned == [], (rel, unpinned)
    assert "memory-boost==" in (ROOT / "llms-install.md").read_text(encoding="utf-8")


def test_mcp_registry_entry_matches_package_and_readme():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    server = _json("server.json")
    [pkg] = server["packages"]
    assert server["version"] == pkg["version"] == version
    assert pkg["registryType"] == "pypi" and pkg["identifier"] == "memory-boost"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {server['name']} -->" in readme  # the registry's ownership check
    assert len(server["description"]) <= 100


def test_readme_has_no_relative_links_or_images():
    """PyPI and the MCP registry render README.md outside the repo: relative paths break there."""
    import re
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r'(?:src|href)="([^"]+)"', readme) + re.findall(r"\]\(([^)]+)\)", readme)
    relative = [t for t in targets if not t.startswith(("http://", "https://", "#", "mailto:"))]
    assert relative == []


def test_version_is_the_same_everywhere():
    from memory_boost import __version__
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert __version__ == version
    assert f"## {version} " in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_plugin_folder_meets_the_directory_checklist():
    """claude.com/docs/plugins/pre-submission-checklist: README >= 40 words outside code, a LICENSE,
    non-image files <= 256 KiB, <= 512 files, no OS junk. The plugin folder is the repo root."""
    import re
    import shutil
    import subprocess

    import pytest
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        pytest.skip("needs the git checkout")
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert len(files) <= 512
    images = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
    big = [f for f in files if not f.lower().endswith(images) and (ROOT / f).stat().st_size > 256 * 1024]
    assert big == []
    junk = {".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX", "uv.toml", ".npmrc"}
    assert [f for f in files if junk & set(f.split("/"))] == []
    prose = re.sub(r"```.*?```", "", (ROOT / "README.md").read_text(encoding="utf-8"), flags=re.S)
    assert len(re.findall(r"[A-Za-z]{2,}", prose)) >= 40
    assert (ROOT / "LICENSE").is_file() and _json(".claude-plugin/plugin.json")["license"] == "MIT"
