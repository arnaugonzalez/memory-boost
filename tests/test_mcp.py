"""Protocol-level test: start `memory-boost serve` over stdio and call every tool."""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TOOLS = {"memory_brief", "memory_recall", "memory_page", "memory_save", "memory_drift", "memory_lesson",
         "memory_checkpoint", "memory_resume"}


async def _session_calls():
    params = StdioServerParameters(command=sys.executable, args=["-m", "memory_boost.cli", "serve"],
                                   env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = {t.name: t for t in (await s.list_tools()).tools}
        out = {}
        out["brief"] = await s.call_tool("memory_brief", {"project": "acme-api"})
        out["save"] = await s.call_tool("memory_save", {"project": "acme-api", "action": "mcp probe",
                                                        "agent": "test"})
        out["recall"] = await s.call_tool("memory_recall", {"query": "mcp probe"})
        out["page"] = await s.call_tool("memory_page", {"name": "acme-api", "section": "gotchas"})
        out["ckpt"] = await s.call_tool("memory_checkpoint", {"project": "acme-api", "session_id": "t1",
                                                              "task": "probe", "done": ["x"]})
        out["resume"] = await s.call_tool("memory_resume", {"project": "acme-api", "session_id": "t1"})
        out["bad"] = await s.call_tool("memory_page", {"name": "../../etc/passwd"})
        return tools, out


def text(result):
    return "".join(c.text for c in result.content)


def test_all_tools_over_stdio(home):
    tools, out = asyncio.run(_session_calls())
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.description and t.inputSchema["type"] == "object"
    assert tools["memory_save"].inputSchema["required"] == ["project", "action"]
    assert "acme-api — context" in text(out["brief"])
    assert text(out["save"]) == "saved: acme-api | mcp probe"
    assert "mcp probe" in text(out["recall"])
    assert "integer cents" in text(out["page"])
    assert "done=1" in text(out["ckpt"])
    assert "**Task:** probe" in text(out["resume"])
    assert out["bad"].isError and "invalid page name" in text(out["bad"])
