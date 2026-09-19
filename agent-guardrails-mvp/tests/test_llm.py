"""模型层：协议归一化、重试边界、证据留痕，以及"密钥绝不落地"。"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent_guardrails.config import Config
from agent_guardrails.llm import DeepSeekClient, LLMError, ScriptedClient

SECRET_KEY = "sk-unit-test-secret-do-not-log"


class FakeResponse:
    def __init__(self, payload: Dict[str, Any], status: int = 200) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class FakeOpener:
    """按顺序返回预设响应，并记下每次请求体 —— 不触网。"""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.bodies: List[Dict[str, Any]] = []

    def __call__(self, request: Any, timeout: Optional[float] = None) -> FakeResponse:
        if request.data:
            self.bodies.append(json.loads(request.data.decode("utf-8")))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, FakeResponse) else FakeResponse(item)


def reply(
    text: str = "OK",
    *,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    usage: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {"content": text}
    if tool_calls:
        message["tool_calls"] = [
            {"id": call["id"], "function": {"name": call["name"], "arguments": json.dumps(call["args"])}}
            for call in tool_calls
        ]
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.fixture
def config() -> Config:
    return Config(api_key=SECRET_KEY, model="deepseek-flash", max_retries=3)


def test_completion_is_normalised_including_tool_calls(monkeypatch: Any, config: Config) -> None:
    opener = FakeOpener(
        reply(
            "调用工具", tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "/workspace/a.txt"}}]
        )
    )
    monkeypatch.setattr("agent_guardrails.llm.urllib.request.urlopen", opener)
    client = DeepSeekClient(config)
    completion = client.complete([{"role": "user", "content": "读文件"}], tools=[{"type": "function"}])
    assert completion.text == "调用工具"
    assert completion.tool_calls[0].name == "read_file"
    assert completion.tool_calls[0].arguments == {"path": "/workspace/a.txt"}
    assert completion.prompt_tokens == 10
    assert completion.tool_names == ["read_file"]
    assert opener.bodies[0]["tools"] == [{"type": "function"}]
    assert opener.bodies[0]["tool_choice"] == "auto"
    assert opener.bodies[0]["stream"] is False


def test_http_error_that_is_not_retryable_fails_immediately(monkeypatch: Any, config: Config) -> None:
    error = urllib.error.HTTPError(
        "https://api.deepseek.com/chat/completions",
        401,
        "Unauthorized",
        {},
        io.BytesIO(b'{"error":"bad key"}'),
    )
    opener = FakeOpener(error, reply())  # 第二个响应不该被用到
    monkeypatch.setattr("agent_guardrails.llm.urllib.request.urlopen", opener)
    with pytest.raises(LLMError) as excinfo:
        DeepSeekClient(config).complete([{"role": "user", "content": "hi"}])
    assert "HTTP 401" in str(excinfo.value)


def test_rate_limit_is_retried_with_backoff(monkeypatch: Any, config: Config) -> None:
    error = urllib.error.HTTPError(
        "https://api.deepseek.com/chat/completions",
        429,
        "Too Many Requests",
        {},
        io.BytesIO(b'{"error":"slow down"}'),
    )
    opener = FakeOpener(error, reply("第二次成功"))
    sleeps: List[float] = []
    monkeypatch.setattr("agent_guardrails.llm.urllib.request.urlopen", opener)
    client = DeepSeekClient(config, sleep=sleeps.append, random_uniform=lambda a, b: 0.0)
    completion = client.complete([{"role": "user", "content": "hi"}])
    assert completion.text == "第二次成功"
    assert sleeps == [0.5]
    assert [exchange["status"] for exchange in client.exchanges] == [429, 200]


def test_transport_errors_are_retried_then_give_up(monkeypatch: Any, config: Config) -> None:
    opener = FakeOpener(*[urllib.error.URLError("connection reset") for _ in range(3)])
    monkeypatch.setattr("agent_guardrails.llm.urllib.request.urlopen", opener)
    client = DeepSeekClient(config, sleep=lambda _: None, random_uniform=lambda a, b: 0.0)
    with pytest.raises(LLMError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert "重试 3 次仍失败" in str(excinfo.value)
    assert len(client.exchanges) == 3


def test_response_without_choices_is_an_error(monkeypatch: Any, config: Config) -> None:
    opener = FakeOpener({"error": "weird"})
    monkeypatch.setattr("agent_guardrails.llm.urllib.request.urlopen", opener)
    with pytest.raises(LLMError):
        DeepSeekClient(config).complete([{"role": "user", "content": "hi"}])


def test_wire_file_is_written_and_key_never_appears_in_logs(
    monkeypatch: Any, config: Config, tmp_path: Path, log_file: Path
) -> None:
    """密钥只在请求头里，绝不进日志与证据文件 —— 这条断言是安全要求，不是文档承诺。"""
    wire = tmp_path / "wire.jsonl"
    opener = FakeOpener(reply("OK"))
    monkeypatch.setattr("agent_guardrails.llm.urllib.request.urlopen", opener)
    DeepSeekClient(config, wire_path=wire).complete([{"role": "user", "content": "hi"}])

    wire_text = wire.read_text(encoding="utf-8")
    log_text = log_file.read_text(encoding="utf-8")
    assert SECRET_KEY not in wire_text and SECRET_KEY not in log_text
    assert "Bearer" not in wire_text and "Bearer" not in log_text
    exchange = json.loads(wire_text.splitlines()[0])
    assert exchange["status"] == 200 and exchange["response_raw"]


def test_masked_key_exposes_at_most_four_characters() -> None:
    assert Config(api_key=SECRET_KEY).masked_key() == "****" + SECRET_KEY[-4:]
    assert Config(api_key="ab").masked_key() == "****"
    assert SECRET_KEY not in repr(Config(api_key=SECRET_KEY))


def test_scripted_client_replays_tool_calls_then_final_text() -> None:
    client = ScriptedClient(
        [
            {"tool_calls": [{"name": "read_file", "arguments": {"path": "/workspace/a.txt"}}]},
            "任务完成",
        ]
    )
    first = client.complete([{"role": "user", "content": "读文件"}])
    second = client.complete([{"role": "user", "content": "读文件"}])
    assert first.tool_calls[0].name == "read_file"
    assert first.finish_reason == "tool_calls"
    assert second.text == "任务完成"
    assert second.finish_reason == "stop"
    assert client.remaining == 0
    assert len(client.calls) == 2


def test_scripted_client_refuses_to_invent_output_when_exhausted() -> None:
    client = ScriptedClient([])
    with pytest.raises(LLMError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}])
    assert "脚本已用尽" in str(excinfo.value)


def test_scripted_client_records_wire_evidence() -> None:
    """脚本化替身也要留痕：离线演练的证据格式与真实调用一致。"""
    client = ScriptedClient(["完成"], label="unit")
    client.complete([{"role": "user", "content": "hi"}])
    exchange = client.exchanges[0]
    assert exchange["request"]["scripted"] is True
    assert json.loads(exchange["response_raw"])["choices"][0]["message"]["content"] == "完成"
