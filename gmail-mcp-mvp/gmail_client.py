"""Thin Gmail API client used by the MCP server.

Design goals:
- Pure transformation functions (parse/format) are separated from the
  googleapiclient service object so they can be unit-tested without network.
- GmailClient accepts an injected ``service`` (duck-typed), which lets tests
  substitute a fake and lets the server run in --test mode end-to-end.

Auth is handled elsewhere: setup_oauth.py mints token.json; this module only
consumes it via build_service().
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Optional

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Tool results are capped so a single MCP call stays small (bounded context).
MAX_THREAD_RESULTS = 20
MAX_MESSAGE_BYTES = 100_000  # decoded body cap per message


def _header_value(headers: list[dict], name: str) -> Optional[str]:
    """Extract a header value by name (case-insensitive), Gmail API style."""
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def decode_body(msg: dict) -> str:
    """Decode a Gmail message body (base64url, may be multipart).

    Returns plain-text if available, else HTML, else empty string.
    The result is truncated to MAX_MESSAGE_BYTES to bound response size.
    """
    payload = msg.get("payload") or {}
    body = _walk_payload(payload, prefer="text/plain") or _walk_payload(payload, prefer="text/html")
    if body is None:
        return ""
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    return text[:MAX_MESSAGE_BYTES]


def _walk_payload(payload: dict, prefer: str) -> Optional[str]:
    """Depth-first search for a body part whose mimeType contains ``prefer``."""
    mime = (payload.get("mimeType") or "").lower()
    if prefer in mime and payload.get("body", {}).get("data"):
        return payload["body"]["data"]
    for part in payload.get("parts") or []:
        found = _walk_payload(part, prefer)
        if found is not None:
            return found
    return None


def format_message(msg: dict, include_body: bool = False) -> dict:
    """Normalize a Gmail message resource into the MCP tool's output shape."""
    payload = msg.get("payload") or {}
    headers = payload.get("headers") or []
    out = {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "labelIds": msg.get("labelIds", []),
        "internalDate": msg.get("internalDate"),
        "snippet": msg.get("snippet", ""),
        "subject": _header_value(headers, "Subject") or "",
        "from": _header_value(headers, "From") or "",
        "to": _header_value(headers, "To") or "",
        "date": _header_value(headers, "Date") or "",
    }
    if include_body:
        out["body"] = decode_body(msg)
    return out


def format_thread(thread: dict, include_body: bool = False) -> dict:
    """Normalize a Gmail thread resource (messages already fetched) for output."""
    return {
        "id": thread.get("id"),
        "historyId": thread.get("historyId"),
        "messages": [
            format_message(m, include_body=include_body) for m in (thread.get("messages") or [])
        ],
    }


class GmailClient:
    """Read-only facade over the Gmail API v1 service object."""

    def __init__(self, service: Any, user_id: str = "me"):
        self._service = service
        self._user_id = user_id

    def list_labels(self) -> list[dict]:
        resp = self._service.users().labels().list(userId=self._user_id).execute()
        labels = resp.get("labels", [])
        return [
            {"id": l.get("id"), "name": l.get("name"), "type": l.get("type")} for l in labels
        ]

    def search_threads(self, query: str = "", max_results: int = 10) -> list[dict]:
        """Search threads with Gmail query syntax; returns summaries (no bodies)."""
        n = min(max_results, MAX_THREAD_RESULTS)
        resp = (
            self._service.users()
            .threads()
            .list(userId=self._user_id, q=query or None, maxResults=n)
            .execute()
        )
        threads = resp.get("threads", [])
        out = []
        for t in threads:
            tid = t["id"]
            full = (
                self._service.users()
                .threads()
                .get(userId=self._user_id, id=tid, format="metadata")
                .execute()
            )
            out.append(format_thread(full, include_body=False))
        return out

    def get_thread(self, thread_id: str, include_body: bool = False) -> dict:
        fmt = "full" if include_body else "metadata"
        thread = (
            self._service.users()
            .threads()
            .get(userId=self._user_id, id=thread_id, format=fmt)
            .execute()
        )
        return format_thread(thread, include_body=include_body)


def load_token(token_path: Path) -> dict:
    return json.loads(token_path.read_text())


def build_service(token_path: Path):
    """Build an authorized googleapiclient service from a cached token.json.

    Raises FileNotFoundError with a clear hint when the user has not run
    setup_oauth.py yet — the server must fail loudly, not silently.
    """
    if not token_path.exists():
        raise FileNotFoundError(
            f"No OAuth token at {token_path}. Run `uv run python setup_oauth.py` first "
            "(see README.md, section 快速开始)."
        )
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)
