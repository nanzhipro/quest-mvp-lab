"""In-memory fake Gmail service + client for tests and --test mode.

Shapes mimic googleapiclient's nested call style:
    service.users().labels().list(userId=...).execute() -> dict
so GmailClient works against it unchanged.
"""

from __future__ import annotations

import base64

from gmail_client import GmailClient

# Encodings are computed, not hand-written, so fixtures can never drift from
# the text they claim to represent.
_B64 = lambda s: base64.urlsafe_b64encode(s.encode()).decode()

# A thread with a multipart message (text/plain + text/html) to exercise
# decode_body, plus a second message with only HTML.
SAMPLE_MESSAGES = [
    {
        "id": "msg1",
        "threadId": "thread1",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1756000000000",
        "snippet": "Meeting moved to 3pm",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": "Re: weekly sync"},
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Fri, 1 Aug 2025 10:00:00 +0800"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _B64("Hello Alice. Good point!")}},
                {"mimeType": "text/html", "body": {"data": _B64("<b>Hello</b>")}},
            ],
        },
    },
    {
        "id": "msg2",
        "threadId": "thread1",
        "labelIds": ["INBOX"],
        "internalDate": "1756000100000",
        "snippet": "Sent",
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "Subject", "value": "Re: weekly sync"},
                {"name": "From", "value": "me@example.com"},
                {"name": "Date", "value": "Fri, 1 Aug 2025 10:05:00 +0800"},
            ],
            "body": {"data": _B64("<p>Ok!</p>")},
        },
    },
]

SAMPLE_LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "UNREAD", "name": "UNREAD", "type": "system"},
    {"id": "Label_5", "name": "work", "type": "user"},
]

THREADS_BY_ID = {"thread1": {"id": "thread1", "historyId": "h1", "messages": SAMPLE_MESSAGES}}


class _Call:
    """A chained callable: users().threads().get(id=...).execute()."""

    def __init__(self, fn, **kwargs):
        self._fn = fn
        self._kwargs = kwargs

    def execute(self):
        return self._fn(**self._kwargs)


class FakeGmailService:
    """Mimics the googleapiclient service surface used by GmailClient."""

    def __init__(self):
        self._last_query = None

    def _labels_list(self, **kw):
        return {"labels": SAMPLE_LABELS}

    def _threads_list(self, userId=None, q=None, maxResults=None, **kw):
        self._last_query = q
        items = [{"id": tid} for tid in THREADS_BY_ID]
        return {"threads": items[:maxResults]}

    def _threads_get(self, userId=None, id=None, format=None, **kw):
        thread = dict(THREADS_BY_ID[id])
        return thread

    # GmailClient only uses these three calls:
    #   users().labels().list(userId=...)
    #   users().threads().list(userId=..., q=..., maxResults=...)
    #   users().threads().get(userId=..., id=..., format=...)
    def users(self):
        return _Users(self)


class _Labels:
    def __init__(self, svc: FakeGmailService):
        self._svc = svc

    def list(self, **kw):
        return _Call(self._svc._labels_list, **kw)


class _Threads:
    def __init__(self, svc: FakeGmailService):
        self._svc = svc

    def list(self, **kw):
        return _Call(self._svc._threads_list, **kw)

    def get(self, **kw):
        return _Call(self._svc._threads_get, **kw)


class _Users:
    def __init__(self, svc: FakeGmailService):
        self._svc = svc

    def labels(self):
        return _Labels(self._svc)

    def threads(self):
        return _Threads(self._svc)


class FakeGmailClient(GmailClient):
    """Drop-in client used by the MCP server in GMAIL_MCP_TEST=1 mode."""

    def __init__(self):
        super().__init__(FakeGmailService())
