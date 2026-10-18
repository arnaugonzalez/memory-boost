"""Protocol-level test: start `memory-boost serve` over stdio and call every tool."""
import asyncio
import os
import socket
import subprocess
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from memory_boost import __version__

TOOLS = {"memory_brief", "memory_recall", "memory_page", "memory_save", "memory_drift", "memory_lesson",
         "memory_checkpoint", "memory_resume", "memory_shelf"}


async def _session_calls():
    params = StdioServerParameters(command=sys.executable, args=["-m", "memory_boost.cli", "serve"],
                                   env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        tools = {t.name: t for t in (await s.list_tools()).tools}
        out = {"init": init}
        out["brief"] = await s.call_tool("memory_brief", {"project": "acme-api"})
        out["save"] = await s.call_tool("memory_save", {"project": "acme-api", "action": "mcp probe",
                                                        "agent": "test"})
        out["recall"] = await s.call_tool("memory_recall", {"query": "mcp probe"})
        out["page"] = await s.call_tool("memory_page", {"name": "acme-api", "section": "gotchas"})
        out["ckpt"] = await s.call_tool("memory_checkpoint", {"project": "acme-api", "session_id": "t1",
                                                              "task": "probe", "done": ["x"]})
        out["resume"] = await s.call_tool("memory_resume", {"project": "acme-api", "session_id": "t1"})
        out["shelf"] = await s.call_tool("memory_shelf", {"text": "mcp idea", "project": "acme-api"})
        out["bad"] = await s.call_tool("memory_page", {"name": "../../etc/passwd"})
        return tools, out


def text(result):
    return "".join(c.text for c in result.content)


def test_all_tools_over_stdio(home):
    tools, out = asyncio.run(_session_calls())
    assert set(tools) == TOOLS
    info = out["init"].serverInfo
    assert (info.name, info.version) == ("memory-boost", __version__)
    for t in tools.values():
        assert t.description and t.inputSchema["type"] == "object"
    assert tools["memory_save"].inputSchema["required"] == ["project", "action"]
    assert "acme-api — context" in text(out["brief"])
    assert text(out["save"]) == "saved: acme-api | mcp probe"
    assert "mcp probe" in text(out["recall"])
    assert "integer cents" in text(out["page"])
    assert "done=1" in text(out["ckpt"])
    assert "**Task:** probe" in text(out["resume"])
    assert text(out["shelf"]) == "shelved: concepts/idea-shelf.md | - [2026-09-24] (acme-api) mcp idea"
    assert out["bad"].isError and "invalid page name" in text(out["bad"])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _http_calls(url: str):
    async with streamable_http_client(url) as (r, w, _), ClientSession(r, w) as s:
        init = await s.initialize()
        tools = {t.name for t in (await s.list_tools()).tools}
        page = await s.call_tool("memory_page", {"name": "acme-api", "section": "gotchas"})
        return init.serverInfo, tools, page


def test_all_tools_over_http(home):
    port = _free_port()
    cmd = [sys.executable, "-m", "memory_boost.cli", "serve", "--http", "--port", str(port)]
    proc = subprocess.Popen(cmd, env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while True:
            assert proc.poll() is None, proc.stderr.read().decode()
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "serve --http did not start listening"
                time.sleep(0.1)
        info, tools, page = asyncio.run(_http_calls(f"http://127.0.0.1:{port}/mcp"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert (info.name, info.version) == ("memory-boost", __version__)
    assert tools == TOOLS
    assert "integer cents" in text(page)
