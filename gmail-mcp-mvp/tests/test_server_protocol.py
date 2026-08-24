"""End-to-end MCP protocol tests: spawn the real server (test mode) over stdio
and exercise tools/list + tools/call through the MCP wire protocol.

This validates the full chain Hermes uses — JSON-RPC over stdio → FastMCP
dispatch → GmailClient → (fake) Gmail service — without needing a real token.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.anyio
async def test_full_protocol_roundtrip():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["GMAIL_MCP_TEST"] = "1"
    env["PYTHONPATH"] = str(PROJECT_DIR)  # server imports tests.fake_gmail

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_DIR / "gmail_mcp_server.py")],
        env=env,
        cwd=str(PROJECT_DIR),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # --- tools/list: server exposes exactly the 3 read-only tools ---
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert names == {"list_labels", "search_threads", "get_thread"}, names
            search = next(t for t in tools.tools if t.name == "search_threads")
            assert "query" in search.inputSchema["properties"]
            assert "max_results" in search.inputSchema["properties"]

            # --- tools/call: list_labels (enveloped: {"labels": [...]}) ---
            res = await session.call_tool("list_labels", {})
            assert not res.isError
            labels: list = _load_result(res)["labels"]
            assert {l["name"] for l in labels} >= {"INBOX", "UNREAD", "work"}

            # --- tools/call: search_threads with query ---
            res = await session.call_tool(
                "search_threads", {"query": "from:alice", "max_results": 5}
            )
            assert not res.isError
            threads: list = _load_result(res)["threads"]
            assert len(threads) == 1
            first: dict = threads[0]
            assert first["id"] == "thread1"
            assert first["messages"][0]["from"] == "Alice <alice@example.com>"
            assert "body" not in first["messages"][0]

            # --- tools/call: get_thread with include_body ---
            res = await session.call_tool(
                "get_thread", {"thread_id": "thread1", "include_body": True}
            )
            assert not res.isError
            thread: dict = _load_result(res)["thread"]
            assert thread["id"] == "thread1"
            assert thread["messages"][0]["body"] == "Hello Alice. Good point!"

            # --- tools/call: unknown tool → clean protocol error ---
            res = await session.call_tool("send_email", {})
            assert res.isError


def _load_result(res: Any) -> Any:
    """Unpack an MCP CallToolResult into Python.

    FastMCP splits a returned list into one text content per element, so we
    aggregate: a single content parses as-is; multiple contents join into a
    list.
    """
    items = []
    for content in res.content:
        if getattr(content, "type", None) == "text":
            items.append(json.loads(content.text))
    if not items:
        raise AssertionError(f"no text content in result: {res}")
    if len(items) == 1 and not isinstance(items[0], list):
        return items[0]
    merged: list[Any] = []
    for it in items:
        merged.extend(it) if isinstance(it, list) else merged.append(it)
    return merged
