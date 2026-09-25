#!/usr/bin/env bash
# Runs the README quickstart against the `memory-boost` on PATH, in a throwaway HOME,
# plus the hook payload and an MCP handshake. Used by CI, the release workflow and the
# clean-container check. Exit 0 = a stranger following the README gets a working tool.
set -euo pipefail

HOME="$(mktemp -d)"
export HOME
unset MEMORY_BOOST_HOME MEMORY_BOOST_WIKI MEMORY_BOOST_INDEX MEMORY_BOOST_CHECKPOINTS \
      MEMORY_BOOST_TRANSCRIPTS XDG_DATA_HOME XDG_CACHE_HOME
data="$HOME/.local/share/memory-boost"   # the path the README documents

step() { printf '\n== %s\n' "$*" >&2; }

step version;  memory-boost --version
step init;     memory-boost init --example
step drift;    memory-boost drift | tee /dev/stderr | grep -q "pin-postgres-15"
step mine;     memory-boost mine --root "$data/example_transcripts" | grep -q "sessions      3"
step lessons;  memory-boost lessons --project acme-api | grep -q "retry-jobs-idempotently"
step recall
memory-boost recall postgres --json | python3 -c 'import json,sys; assert json.load(sys.stdin)["version"] == 1'
step "page missing exits 1"
if memory-boost page does-not-exist 2>/dev/null; then echo "expected exit 1" >&2; exit 1; fi
step hook
echo '{"cwd":"/work/acme-api","session_id":"smoke","source":"startup"}' \
  | memory-boost hook session-start \
  | python3 -c 'import json,sys; c = json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]; assert "acme-api" in c and "Lessons from other projects" in c'
step "mcp handshake"
python3 - <<'EOF'
import json, subprocess

p = subprocess.Popen(["memory-boost", "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

def call(msg, reply=True):
    p.stdin.write(json.dumps(msg) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline()) if reply else None

init = call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "smoke", "version": "0"}}})
assert init["result"]["serverInfo"]["name"], init
call({"jsonrpc": "2.0", "method": "notifications/initialized"}, reply=False)
tools = {t["name"] for t in call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
p.terminate()
assert len(tools) == 8 and "memory_drift" in tools, tools
print("mcp tools:", ", ".join(sorted(tools)))
EOF
step OK
