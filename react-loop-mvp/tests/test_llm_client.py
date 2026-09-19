"""llm.py — the HTTP surface, exercised against a fake transport (no network)."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any, Dict, List, Optional

import pytest

from react_loop_mvp.config import Config
from react_loop_mvp.llm import (
    RETRYABLE_STATUS,
    DeepSeekClient,
    LLMError,
    ScriptedClient,
    _normalise,
    _parse_tool_calls,
)


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class RawResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "RawResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def http_error(code: int, body: bytes = b'{"error": "boom"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.deepseek.com/chat/completions", code, "err", {}, io.BytesIO(body)
    )


def reply_payload(
    content: str = "hello",
    *,
    reasoning: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    usage: Optional[Dict[str, int]] = None,
    finish_reason: str = "stop",
) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class Transport:
    """Records every request and replays a scripted list of outcomes."""

    def __init__(self, outcomes: List[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: Any, timeout: Optional[float] = None) -> Any:
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": body,
                "headers": {k.lower(): v for k, v in request.header_items()},
                "timeout": timeout,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def client_and_transport(monkeypatch: pytest.MonkeyPatch):
    """Wire a DeepSeekClient to a scripted transport; return (client, transport, sleeps)."""

    def _build(outcomes: List[Any], **kwargs: Any):
        sleeps: List[float] = []
        transport = Transport(outcomes)
        monkeypatch.setattr("urllib.request.urlopen", transport)
        config = Config(api_key="sk-test-key", base_url="https://api.deepseek.com", **kwargs)
        client = DeepSeekClient(config, sleep=sleeps.append, random_uniform=lambda a, b: 0.0)
        return client, transport, sleeps

    return _build


# ── happy path ───────────────────────────────────────────────────────────────
def test_complete_sends_the_documented_payload(client_and_transport: Any) -> None:
    client, transport, _ = client_and_transport([FakeResponse(reply_payload("hi"))], max_retries=1)
    client.complete([{"role": "user", "content": "ping"}])
    request = transport.requests[0]
    assert request["url"] == "https://api.deepseek.com/chat/completions"
    assert request["method"] == "POST"
    assert request["body"]["model"] == "deepseek-chat"
    assert request["body"]["messages"] == [{"role": "user", "content": "ping"}]
    assert request["body"]["stream"] is False
    assert "max_tokens" not in request["body"]  # omitted unless asked for


def test_complete_sends_the_bearer_token(client_and_transport: Any) -> None:
    client, transport, _ = client_and_transport([FakeResponse(reply_payload())], max_retries=1)
    client.complete([{"role": "user", "content": "ping"}])
    assert transport.requests[0]["headers"]["authorization"] == "Bearer sk-test-key"


def test_complete_normalises_content_reasoning_and_usage(client_and_transport: Any) -> None:
    payload = reply_payload(
        "答案", reasoning="思考", usage={"prompt_tokens": 3, "completion_tokens": 2}
    )
    client, _, _ = client_and_transport([FakeResponse(payload)], max_retries=1)
    completion = client.complete([{"role": "user", "content": "q"}])
    assert completion.text == "答案"
    assert completion.reasoning == "思考"
    assert completion.usage == {"prompt_tokens": 3, "completion_tokens": 2}
    assert completion.tokens == 2
    assert completion.finish_reason == "stop"
    assert completion.latency_ms >= 0


def test_tools_are_forwarded_with_auto_choice(client_and_transport: Any) -> None:
    schema = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}]
    client, transport, _ = client_and_transport([FakeResponse(reply_payload())], max_retries=1)
    client.complete([{"role": "user", "content": "q"}], tools=schema)
    assert transport.requests[0]["body"]["tools"] == schema
    assert transport.requests[0]["body"]["tool_choice"] == "auto"


def test_max_tokens_is_sent_when_configured(client_and_transport: Any) -> None:
    client, transport, _ = client_and_transport([FakeResponse(reply_payload())], max_retries=1)
    client.max_tokens = 64
    client.complete([{"role": "user", "content": "q"}])
    assert transport.requests[0]["body"]["max_tokens"] == 64


def test_tool_calls_are_decoded(client_and_transport: Any) -> None:
    payload = reply_payload(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_docs", "arguments": '{"query": "AUTH_OPEN"}'},
            }
        ],
        finish_reason="tool_calls",
    )
    client, _, _ = client_and_transport([FakeResponse(payload)], max_retries=1)
    completion = client.complete([{"role": "user", "content": "q"}])
    assert completion.finish_reason == "tool_calls"
    call = completion.tool_calls[0]
    assert (call.name, call.arguments, call.call_id) == (
        "search_docs",
        {"query": "AUTH_OPEN"},
        "call_1",
    )


def test_malformed_argument_json_degrades_to_an_empty_mapping() -> None:
    calls = _parse_tool_calls([{"id": "x", "function": {"name": "t", "arguments": "{not json"}}])
    assert calls[0].name == "t"
    assert calls[0].arguments == {}
    assert calls[0].arguments_raw == "{not json"


def test_arguments_of_a_non_object_json_are_dropped() -> None:
    calls = _parse_tool_calls([{"function": {"name": "t", "arguments": "[1, 2]"}}])
    assert calls[0].arguments == {}


def test_empty_arguments_become_an_empty_mapping() -> None:
    calls = _parse_tool_calls([{"function": {"name": "t", "arguments": "   "}}])
    assert calls[0].arguments == {}


def test_exchanges_record_the_raw_pair(client_and_transport: Any) -> None:
    client, _, _ = client_and_transport([FakeResponse(reply_payload("ok"))], max_retries=1)
    client.complete([{"role": "user", "content": "q"}])
    exchange = client.exchanges[0]
    assert exchange["status"] == 200
    assert exchange["request"]["path"] == "/chat/completions"
    assert json.loads(exchange["response_raw"])["choices"][0]["message"]["content"] == "ok"
    assert "sk-test-key" not in json.dumps(exchange)


def test_list_models_parses_the_ids(client_and_transport: Any) -> None:
    client, transport, _ = client_and_transport(
        [FakeResponse({"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})],
        max_retries=1,
    )
    assert client.list_models() == ["deepseek-chat", "deepseek-reasoner"]
    assert transport.requests[0]["url"].endswith("/models")
    assert transport.requests[0]["method"] == "GET"


# ── failures ─────────────────────────────────────────────────────────────────
def test_transient_server_error_is_retried_then_succeeds(client_and_transport: Any) -> None:
    client, transport, sleeps = client_and_transport(
        [http_error(500), FakeResponse(reply_payload("second try"))], max_retries=2
    )
    assert client.complete([{"role": "user", "content": "q"}]).text == "second try"
    assert len(transport.requests) == 2
    assert len(sleeps) == 1 and sleeps[0] > 0
    assert [e["status"] for e in client.exchanges] == [500, 200]


def test_client_error_is_not_retried(client_and_transport: Any) -> None:
    client, transport, sleeps = client_and_transport([http_error(401)], max_retries=3)
    with pytest.raises(LLMError) as excinfo:
        client.complete([{"role": "user", "content": "q"}])
    assert "HTTP 401" in str(excinfo.value)
    assert len(transport.requests) == 1
    assert sleeps == []


def test_rate_limit_is_retried_until_the_budget_runs_out(client_and_transport: Any) -> None:
    client, transport, sleeps = client_and_transport(
        [http_error(429), http_error(429)], max_retries=2
    )
    with pytest.raises(LLMError) as excinfo:
        client.complete([{"role": "user", "content": "q"}])
    assert "giving up after 2 attempt(s)" in str(excinfo.value)
    assert len(transport.requests) == 2
    assert len(sleeps) == 1


def test_transport_error_is_retried(client_and_transport: Any) -> None:
    client, _transport, _sleeps = client_and_transport(
        [urllib.error.URLError("dns"), FakeResponse(reply_payload("back"))], max_retries=2
    )
    assert client.complete([{"role": "user", "content": "q"}]).text == "back"
    assert client.exchanges[0]["error"].startswith("URLError")


def test_non_json_body_is_rejected(client_and_transport: Any) -> None:
    client, _, _ = client_and_transport([RawResponse(b"<html>502</html>")], max_retries=1)
    with pytest.raises(LLMError) as excinfo:
        client.complete([{"role": "user", "content": "q"}])
    assert "non-JSON response body" in str(excinfo.value)


def test_response_without_choices_is_rejected() -> None:
    with pytest.raises(LLMError):
        _normalise({"usage": {}}, 1)


def test_backoff_is_exponential_and_capped(client_and_transport: Any) -> None:
    client, _, _ = client_and_transport([], max_retries=1)
    delays = [client._backoff(attempt) for attempt in range(1, 6)]
    assert delays[0] == 0.5
    assert delays[1] == 1.0
    assert delays[-1] <= 8.25


def test_retryable_status_set_matches_the_documented_policy() -> None:
    assert {408, 429, 500, 502, 503, 504} <= RETRYABLE_STATUS
    assert 400 not in RETRYABLE_STATUS
    assert 401 not in RETRYABLE_STATUS
    assert 403 not in RETRYABLE_STATUS


# ── scripted model ───────────────────────────────────────────────────────────
def test_scripted_client_replays_in_order() -> None:
    client = ScriptedClient(["first", "second"])
    assert client.complete([]).text == "first"
    assert client.complete([]).text == "second"
    assert client.remaining == 0


def test_scripted_client_refuses_to_invent_a_reply() -> None:
    client = ScriptedClient(["only"])
    client.complete([])
    with pytest.raises(LLMError):
        client.complete([])


def test_scripted_client_records_prompts_and_tool_calls() -> None:
    client = ScriptedClient(
        [
            {
                "content": "",
                "tool_calls": [
                    {"id": "c1", "name": "calculator", "arguments": '{"expression": "1+1"}'}
                ],
            }
        ]
    )
    completion = client.complete([{"role": "user", "content": "grow me"}])
    assert completion.tool_calls[0].arguments == {"expression": "1+1"}
    assert client.prompts[0][0]["content"] == "grow me"
    assert client.exchanges[0]["endpoint"].startswith("scripted://")
