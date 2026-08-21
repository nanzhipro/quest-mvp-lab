"""Tests for aicode.api — SSE streaming, retries, and response normalization."""

import io
import json
import urllib.error

import pytest

from aicode.api import APIError, ChatClient


class FakeJSON:
    """Non-streaming fake: returns the payload via read()."""

    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


class FakeSSE:
    """Streaming fake: renders payloads as SSE chunks; supports fragmented deltas."""

    def __init__(self, *chunks, extra_lines=None):
        self._lines = []
        for chunk in chunks:
            self._lines.append("data: " + json.dumps(chunk) + "\n")
            self._lines.append("\n")
        for line in extra_lines or []:
            self._lines.append(line)
        self._lines.append("data: [DONE]\n")
        self._lines.append("\n")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def chat_chunk(message=None, delta=None, finish_reason="stop", usage=None):
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": message,
                "delta": delta,
            }
        ],
        "usage": usage or {},
    }


def delta_chunk(finish_reason=None, content=None, reasoning=None, tool_calls=None, usage=None):
    chunk = {"choices": [{"finish_reason": finish_reason, "delta": {}}], "usage": usage}
    if content:
        chunk["choices"][0]["delta"]["content"] = content
    if reasoning:
        chunk["choices"][0]["delta"]["reasoning_content"] = reasoning
    if tool_calls:
        chunk["choices"][0]["delta"]["tool_calls"] = tool_calls
    return chunk


@pytest.fixture
def client():
    return ChatClient(
        base_url="https://example.test/v1/",
        api_key="sk-test",
        model="m1",
        max_tokens=128,
        max_retries=0,
        retry_backoff=0,
    )


# ── normalization ─────────────────────────────────────────────────────────


def test_stream_chat_normalizes_content_and_reasoning(client, monkeypatch):
    monkeypatch.setattr(
        "aicode.api.urllib.request.urlopen",
        lambda req, timeout: FakeSSE(
            delta_chunk(
                reasoning="thinking...",
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            ),
            delta_chunk(content="hi"),
        ),
    )
    resp = client.chat([{"role": "user", "content": "x"}])
    assert resp["content"] == "hi"
    assert resp["reasoning"] == "thinking..."
    assert resp["tool_calls"] == []
    assert resp["finish_reason"] is None  # finish arrives per-chunk; absent here
    assert resp["usage"]["completion_tokens"] == 3


def test_stream_parses_fragmented_tool_calls(client, monkeypatch):
    """Arguments arriving as SSE fragments must be concatenated per tool-call index."""
    monkeypatch.setattr(
        "aicode.api.urllib.request.urlopen",
        lambda req, timeout: FakeSSE(
            delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_",
                        "type": "function",
                        "function": {"name": "list_", "arguments": ""},
                    }
                ]
            ),
            delta_chunk(
                tool_calls=[
                    {"index": 0, "id": "9abc", "function": {"name": "dir", "arguments": '{"pa'}}
                ]
            ),
            delta_chunk(tool_calls=[{"index": 0, "function": {"arguments": 'th": "/tmp"}'}}]),
            delta_chunk(finish_reason="tool_calls"),
        ),
    )
    resp = client.chat([{"role": "user", "content": "list /tmp"}], tools=[{}])
    assert resp["tool_calls"] == [
        {"id": "call_9abc", "name": "list_dir", "arguments": {"path": "/tmp"}}
    ]
    assert resp["finish_reason"] == "tool_calls"


def test_stream_on_content_callback(client, monkeypatch):
    monkeypatch.setattr(
        "aicode.api.urllib.request.urlopen",
        lambda req, timeout: FakeSSE(
            delta_chunk(content="Hel"),
            delta_chunk(content="lo "),
            delta_chunk(content="world"),
        ),
    )
    pieces = []
    resp = client.chat([{"role": "user", "content": "x"}], on_content=pieces.append)
    assert resp["content"] == "Hello world"
    assert pieces == ["Hel", "lo ", "world"]


def test_stream_tolerates_malformed_arguments_json(client, monkeypatch):
    monkeypatch.setattr(
        "aicode.api.urllib.request.urlopen",
        lambda req, timeout: FakeSSE(
            delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "function": {"name": "read_file", "arguments": "not json"},
                    }
                ]
            ),
            delta_chunk(finish_reason="tool_calls"),
        ),
    )
    resp = client.chat([{"role": "user", "content": "x"}], tools=[{}])
    assert resp["tool_calls"][0]["arguments"] == {"_raw": "not json"}


def test_empty_stream_raises(client, monkeypatch):
    monkeypatch.setattr("aicode.api.urllib.request.urlopen", lambda req, timeout: FakeSSE())
    with pytest.raises(APIError) as exc:
        client.chat([{"role": "user", "content": "x"}])
    assert "empty SSE stream" in str(exc.value)


def test_parse_arguments_handles_all_shapes():
    parse = ChatClient._parse_arguments
    # already-parsed dict (some gateways)
    assert parse({"path": "/tmp"}) == {"path": "/tmp"}
    # JSON string (OpenAI spec)
    assert parse('{"path": "/tmp"}') == {"path": "/tmp"}
    # malformed JSON (literal newline INSIDE a string value) → _raw for salvage
    malformed = '{"path": "/tmp", "content": "line1\nline2"}'
    assert parse(malformed) == {"_raw": malformed}
    # non-dict JSON → _raw
    assert parse("[1, 2]") == {"_raw": "[1, 2]"}
    # empty / None-ish
    assert parse(None) == {"_raw": None}
    assert parse("") == {"_raw": ""}


# ── payload shaping ────────────────────────────────────────────────────────


def test_chat_sends_streaming_payload(client, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["auth"] = req.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeSSE(delta_chunk(content="ok"))

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    client.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["timeout"] == 300
    assert captured["body"]["model"] == "m1"
    assert captured["body"]["max_tokens"] == 128
    assert captured["body"]["stream"] is True
    assert captured["body"]["tools"] == [{"type": "function"}]
    assert "temperature" not in captured["body"]


def test_chat_sends_temperature_when_set(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeSSE(delta_chunk(content="ok"))

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    client = ChatClient("https://example.test/v1", "k", "m", temperature=0.2, max_retries=0)
    client.chat([{"role": "user", "content": "x"}])
    assert captured["body"]["temperature"] == 0.2


# ── error mapping and retries ──────────────────────────────────────────────


def test_timeout_maps_to_api_error(client, monkeypatch):
    def fake_urlopen(req, timeout):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(APIError) as exc:
        client.chat([{"role": "user", "content": "x"}])
    assert exc.value.status == 0
    assert "timed out" in str(exc.value)


def test_connection_reset_maps_to_api_error(client, monkeypatch):
    def fake_urlopen(req, timeout):
        raise ConnectionResetError("connection reset by peer")

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(APIError) as exc:
        client.chat([{"role": "user", "content": "x"}])
    assert exc.value.status == 0
    assert "network error" in str(exc.value)


def test_http_4xx_fails_fast_without_retry(client, monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error": {"message": "Invalid API key"}}'),
        )

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(APIError) as exc:
        client.chat([{"role": "user", "content": "x"}])
    assert exc.value.status == 401
    assert "Invalid API key" in str(exc.value)
    assert len(calls) == 1


def test_retries_on_5xx_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "Busy", {}, io.BytesIO(b"busy"))
        return FakeSSE(delta_chunk(content="recovered"))

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("aicode.api.time.sleep", lambda _s: None)
    client = ChatClient("https://example.test/v1", "k", "m", max_retries=2, retry_backoff=2)
    resp = client.chat([{"role": "user", "content": "x"}])
    assert resp["content"] == "recovered"
    assert len(calls) == 2


def test_retries_exhausted_on_timeout(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(1)
        raise TimeoutError("stall")

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("aicode.api.time.sleep", lambda _s: None)
    client = ChatClient("https://example.test/v1", "k", "m", max_retries=2)
    with pytest.raises(APIError) as exc:
        client.chat([{"role": "user", "content": "x"}])
    assert exc.value.status == 0
    assert "2 retries exhausted" in str(exc.value)
    assert len(calls) == 3


def test_stream_rejection_falls_back_to_nonstreaming(monkeypatch):
    bodies = []

    def fake_urlopen(req, timeout):
        bodies.append(json.loads(req.data.decode("utf-8")))
        if bodies[-1].get("stream"):
            raise urllib.error.HTTPError(
                req.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error": "stream not supported"}'),
            )
        return FakeJSON(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "fallback ok"},
                    }
                ],
                "usage": {},
            }
        )

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    client = ChatClient("https://example.test/v1", "k", "m", max_retries=0)
    resp = client.chat([{"role": "user", "content": "x"}])
    assert resp["content"] == "fallback ok"
    assert bodies[0]["stream"] is True
    assert bodies[1]["stream"] is False


# ── non-streaming JSON endpoints (list_models) ─────────────────────────────


def test_list_models_extracts_ids(client, monkeypatch):
    monkeypatch.setattr(
        "aicode.api.urllib.request.urlopen",
        lambda req, timeout: FakeJSON({"data": [{"id": "qwen3.8-27b"}, {"id": "deepseek-v4"}]}),
    )
    assert client.list_models() == ["qwen3.8-27b", "deepseek-v4"]


def test_list_models_network_error_is_wrapped(client, monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("aicode.api.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(APIError) as exc:
        client.list_models()
    assert exc.value.status == 0
    assert "connection refused" in str(exc.value)
