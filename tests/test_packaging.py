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


def test_plugin_runs_its_own_checkout_and_every_skill_exists():
    plugin = _json(".claude-plugin/plugin.json")
    args = plugin["mcpServers"]["memory"]["args"]
    assert args[:3] == ["--quiet", "--from", "${CLAUDE_PLUGIN_ROOT}"] and args[-1] == "serve"
    hook = plugin["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert '"${CLAUDE_PLUGIN_ROOT}"' in hook and hook.endswith("hook session-start")
    for skill in (ROOT / "skills").iterdir():
        assert (skill / "SKILL.md").is_file(), skill


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
