"""Gmail MCP server (stdio transport, read-only).

Tools mirror Google's official Gmail MCP server names so prompts/skills stay
portable if the project later switches to gmailmcp.googleapis.com:
  - list_labels     (official: list_labels)
  - search_threads  (official: search_threads)
  - get_thread      (official: get_thread)

Auth: OAuth 2.0 installed-app flow, gmail.readonly scope only. The token is
cached at secrets/token.json by setup_oauth.py and auto-refreshed by
build_service().

Test mode: set GMAIL_MCP_TEST=1 to run against a fake in-memory Gmail service
(no token required) — used by tests/test_server_protocol.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from gmail_client import GmailClient, build_service

PROJECT_DIR = Path(__file__).resolve().parent
TOKEN_PATH = PROJECT_DIR / "secrets" / "token.json"

mcp = FastMCP("gmail")


# --------------------------------------------------------------------------
# Tool implementations (bound to a client instance via factory)
# --------------------------------------------------------------------------

def _client() -> GmailClient:
    """Resolve the GmailClient. Test mode swaps in a fake (see tests)."""
    if os.environ.get("GMAIL_MCP_TEST") == "1":
        from tests.fake_gmail import FakeGmailClient

        return FakeGmailClient()
    return GmailClient(build_service(TOKEN_PATH))


def _handlers(client: GmailClient) -> None:
    @mcp.tool()
    def list_labels() -> dict:
        """List Gmail labels (id, name, type: user/system).

        Returns {"labels": [...]}.
        """
        return {"labels": client.list_labels()}

    @mcp.tool()
    def search_threads(query: str = "", max_results: int = 10) -> dict:
        """Search threads using Gmail query syntax (e.g. 'from:alice', 'is:unread', 'after:2026/01/01').

        Returns {"threads": [...]} with thread summaries: subject, sender,
        date, snippet, labels. Bodies are not included — call get_thread for
        full content.
        """
        return {"threads": client.search_threads(query=query, max_results=max_results)}

    @mcp.tool()
    def get_thread(thread_id: str, include_body: bool = False) -> dict:
        """Fetch one thread by ID with all its messages.

        include_body=true returns decoded message bodies (plain text preferred,
        HTML fallback); default returns metadata only (subject/from/date/snippet).
        Returns {"thread": {...}}.
        """
        return {"thread": client.get_thread(thread_id=thread_id, include_body=include_body)}


def main() -> None:
    # Force client construction up front so a missing token fails with a clear
    # error at startup (stdio servers can't easily report mid-flight failures).
    try:
        if os.environ.get("GMAIL_MCP_TEST") != "1":
            build_service(TOKEN_PATH)
        _handlers(_client())
        mcp.run()
    except FileNotFoundError as e:
        print(f"gmail-mcp: {e}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
