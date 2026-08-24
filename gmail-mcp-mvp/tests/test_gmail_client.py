"""Unit tests for gmail_client: pure transforms + GmailClient against a fake service."""

import base64

import pytest

from gmail_client import GmailClient, decode_body, format_message, format_thread
from tests.fake_gmail import FakeGmailService, SAMPLE_MESSAGES, SAMPLE_LABELS


# --------------------------------------------------------------------------
# decode_body
# --------------------------------------------------------------------------

def test_decode_body_multipart_prefers_plain_text():
    msg = SAMPLE_MESSAGES[0]
    assert decode_body(msg) == "Hello Alice. Good point!"


def test_decode_body_html_only_fallback():
    msg = SAMPLE_MESSAGES[1]
    assert decode_body(msg) == "<p>Ok!</p>"


def test_decode_body_empty_payload():
    assert decode_body({}) == ""


def test_decode_body_bad_base64_is_safe():
    msg = {"payload": {"mimeType": "text/plain", "body": {"data": "!!!not-base64!!!"}}}
    assert decode_body(msg) == ""


def test_decode_body_truncates_large_bodies():
    big = base64.urlsafe_b64encode(b"x" * 200_000).decode()
    msg = {"payload": {"mimeType": "text/plain", "body": {"data": big}}}
    assert len(decode_body(msg)) == 100_000


# --------------------------------------------------------------------------
# format_message / format_thread
# --------------------------------------------------------------------------

def test_format_message_extracts_headers():
    out = format_message(SAMPLE_MESSAGES[0])
    assert out["subject"] == "Re: weekly sync"
    assert out["from"] == "Alice <alice@example.com>"
    assert out["date"].startswith("Fri")
    assert out["labelIds"] == ["INBOX", "UNREAD"]
    assert "body" not in out  # metadata by default


def test_format_message_include_body():
    out = format_message(SAMPLE_MESSAGES[0], include_body=True)
    assert out["body"] == "Hello Alice. Good point!"


def test_format_thread_shapes_messages():
    thread = {"id": "thread1", "historyId": "h1", "messages": SAMPLE_MESSAGES}
    out = format_thread(thread)
    assert out["id"] == "thread1"
    assert len(out["messages"]) == 2


# --------------------------------------------------------------------------
# GmailClient against FakeGmailService
# --------------------------------------------------------------------------

@pytest.fixture
def client():
    return GmailClient(FakeGmailService())


def test_list_labels(client):
    labels = client.list_labels()
    assert labels == [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "UNREAD", "name": "UNREAD", "type": "system"},
        {"id": "Label_5", "name": "work", "type": "user"},
    ]
    assert len(labels) == len(SAMPLE_LABELS)


def test_search_threads_returns_summaries_without_bodies(client):
    threads = client.search_threads(query="from:alice", max_results=5)
    assert len(threads) == 1
    t = threads[0]
    assert t["id"] == "thread1"
    assert t["messages"][0]["subject"] == "Re: weekly sync"
    assert "body" not in t["messages"][0]


def test_search_threads_forwards_query(client):
    client.search_threads(query="is:unread newer_than:2d")
    assert client._service._last_query == "is:unread newer_than:2d"


def test_search_threads_caps_max_results(client):
    threads = client.search_threads(max_results=999)
    assert len(threads) == 1  # fake has 1 thread; cap is 20


def test_get_thread_metadata_vs_full(client):
    meta = client.get_thread("thread1", include_body=False)
    assert "body" not in meta["messages"][0]

    full = client.get_thread("thread1", include_body=True)
    assert full["messages"][0]["body"] == "Hello Alice. Good point!"
    assert full["messages"][1]["body"] == "<p>Ok!</p>"
