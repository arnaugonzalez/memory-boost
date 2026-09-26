import json
import os
import subprocess
import sys
from importlib import resources

from memory_boost import __version__, cli, core


def run(args, stdin="", env=None):
    return subprocess.run([sys.executable, "-m", "memory_boost.cli", *args], input=stdin,
                          capture_output=True, text=True, env={**os.environ, **(env or {})})


def test_help_and_version(home):
    assert run(["--help"]).returncode == 0
    assert __version__ in run(["--version"]).stdout


def test_init_example_on_fresh_home(tmp_path):
    env = {"MEMORY_BOOST_HOME": str(tmp_path / "h"), "XDG_CACHE_HOME": str(tmp_path / "c")}
    assert run(["init", "--example"], env=env).returncode == 0
    r = run(["recall", "idempotency"], env=env)
    assert "acme-api" in r.stdout
    again = run(["init", "--example"], env=env)
    assert again.returncode != 0 and "not empty" in again.stderr
    assert run(["init"], env=env).returncode == 0  # plain init is idempotent


def test_cli_error_is_readable(home):
    r = run(["save", "--project", "../x", "--action", "a"])
    assert r.returncode == 2 and r.stderr.startswith("error: invalid project")


def test_hook_session_start_claude_envelope(home, tmp_path):
    ev = {"cwd": str(tmp_path / "acme-api"), "session_id": "abc", "source": "startup"}
    r = run(["hook", "session-start"], stdin=json.dumps(ev))
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "acme-api — context" in out["additionalContext"]
    assert "checkpoint_session=abc" in out["additionalContext"]


def test_hook_after_compact_injects_only_checkpoint(home):
    core.save_checkpoint("acme-api", "abc", "ship v2", next_step="deploy")
    text = cli.session_start_payload({"cwd": "/w/acme-api", "session_id": "abc", "source": "compact"})
    assert text.startswith("## Session checkpoint") and "deploy" in text
    assert "### Index" not in text
    assert cli.session_start_payload({"cwd": "/w/unknown", "source": "compact"}) is None


def test_hook_resume_shows_own_active_checkpoint(home):
    core.save_checkpoint("acme-api", "abc", "ship v2")
    text = cli.session_start_payload({"cwd": "/w/acme-api", "session_id": "abc", "source": "resume"})
    assert "**Task:** ship v2" in text


def test_hook_never_fails(home):
    for stdin in ["", "not json", "[]"]:
        r = run(["hook", "session-start"], stdin=stdin)
        assert r.returncode == 0


def test_hook_text_format(home):
    r = run(["hook", "session-start", "--format", "text"], stdin=json.dumps({"cwd": "/w/acme-api"}))
    assert r.stdout.startswith("## Memory")


def test_deterministic_output(home):
    a = run(["brief", "--project", "acme-api"]).stdout
    b = run(["brief", "--project", "acme-api"]).stdout
    assert a == b and a


def test_drift_mine_lessons_cli(home):
    r = run(["drift"])
    assert r.returncode == 0 and "pin-postgres-15" in r.stdout
    assert json.loads(run(["drift", "--json"]).stdout)["totals"]["findings"] == 5
    assert run(["drift", "--project", "pixel-notes"]).stdout.startswith("- project-no-decisions")
    assert run(["drift", "--project", "nope"]).stdout.strip() == "(nothing drifted)"
    root = str(resources.files("memory_boost") / "example_transcripts")
    r = run(["mine", "--root", root])
    assert r.returncode == 0 and "sessions      3" in r.stdout
    assert json.loads(run(["mine", "--root", root, "--json", "--since", "5d"]).stdout)["sessions"] == 0
    assert "retry-jobs-idempotently" in run(["lessons", "--project", "acme-api"]).stdout
    r = run(["lesson", "--name", "x", "--title", "X", "--applies-when", "fastapi", "--body", "-"],
            stdin="body\n")
    assert r.returncode == 0 and (home / "wiki" / "concepts" / "x.md").exists()
    r = run(["lesson", "--name", "../x", "--title", "X", "--applies-when", "a", "--body", "b"])
    assert r.returncode == 2 and "error:" in r.stderr


def test_agent_contract_exit_codes_and_stderr(home, tmp_path):
    """stdout = data, stderr = errors with a fix line, exit 1 = not found, 2 = usage."""
    r = run(["page", "nope"])
    assert r.returncode == 1 and r.stdout == "" and "not found" in r.stderr and "fix:" in r.stderr
    r = run(["page", "acme-api", "--section", "nope"])
    assert r.returncode == 1 and r.stdout == ""
    r = run(["recall", "zzzqqq-nothing-matches"])
    assert r.returncode == 1 and r.stdout == ""
    r = run(["drift"], env={"MEMORY_BOOST_WIKI": str(tmp_path / "missing")})
    assert r.returncode == 1 and "does not exist" in r.stderr and "init" in r.stderr
    r = run(["mine", "--root", str(tmp_path / "missing")])
    assert r.returncode == 1 and "warning:" in r.stderr and "fix:" in r.stderr
    r = run(["mine", "--root", str(tmp_path / "missing"),
             "--root", str(resources.files("memory_boost") / "example_transcripts")])
    assert r.returncode == 0 and "sessions      3" in r.stdout and "warning:" in r.stderr
    r = run(["lesson", "--name", "../x", "--title", "X", "--applies-when", "a", "--body", "b"])
    assert r.returncode == 2


def test_json_everywhere_with_version(home):
    root = str(resources.files("memory_boost") / "example_transcripts")
    for args in (["brief", "--project", "acme-api"], ["page", "acme-api"], ["recall", "postgres"],
                 ["lessons"], ["lessons", "--project", "acme-api"], ["checkpoints"],
                 ["resume", "--project", "acme-api"], ["drift"], ["mine", "--root", root], ["index"]):
        r = run([*args, "--json"])
        assert r.returncode == 0, args
        assert json.loads(r.stdout)["version"] == 1, args


def test_reports_end_with_next_command_and_help_has_examples(home):
    root = str(resources.files("memory_boost") / "example_transcripts")
    assert "next: memory-boost page pin-postgres-15" in run(["drift"]).stdout
    assert "next: memory-boost lesson" in run(["mine", "--root", root]).stdout
    h = run(["--help"]).stdout
    assert "examples:" in h and "exit codes:" in h
    assert "--json" in run(["brief", "--help"]).stdout
