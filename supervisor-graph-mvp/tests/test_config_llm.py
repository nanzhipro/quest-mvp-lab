"""Config and model-client tests: precedence, secret hygiene, retries, offline replays."""

from __future__ import annotations

import json
import socket
import urllib.error

import pytest

from supervisor_graph_mvp.config import Config, ConfigError
from supervisor_graph_mvp.llm import DeepSeekClient, LLMError, ScriptedClient


# ── config ────────────────────────────────────────────────────────────────────
def test_env_values_are_used_when_no_argument_is_given():
    config = Config.from_env(env={"DEEPSEEK_API_KEY": "env-key", "DEEPSEEK_MODEL": "m1"})
    assert config.api_key == "env-key"
    assert config.model == "m1"
    assert config.base_url == "https://api.deepseek.com"


def test_explicit_arguments_win_over_the_environment():
    config = Config.from_env(
        api_key="explicit",
        model="m2",
        base_url="https://gateway.example/v1/",
        env={"DEEPSEEK_API_KEY": "env-key", "DEEPSEEK_MODEL": "m1"},
    )
    assert config.api_key == "explicit"
    assert config.model == "m2"
    assert config.chat_completions_url == "https://gateway.example/v1/chat/completions"


def test_a_missing_key_is_a_config_error():
    with pytest.raises(ConfigError, match="no API key"):
        Config.from_env(env={})


def test_the_key_never_appears_in_repr_or_logs():
    config = Config.from_env(api_key="sk-secret-value", env={})
    assert "sk-secret-value" not in repr(config)
    assert config.masked_key() == "****alue"
    assert Config.from_env(api_key="abc", env={}).masked_key() == "****"


def test_budgets_have_documented_defaults_and_overrides():
    config = Config.from_env(api_key="k", env={})
    assert (config.max_supersteps, config.max_repairs) == (24, 1)
    assert Config.from_env(api_key="k", max_supersteps=5, max_repairs=0, env={}).max_repairs == 0


def test_endpoint_joining_tolerates_a_trailing_slash():
    config = Config.from_env(api_key="k", base_url="https://x/y/", env={})
    assert config.endpoint("/models") == "https://x/y/models"


# ── the real client ───────────────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, payload: str, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _config(**kwargs) -> Config:
    return Config.from_env(api_key="sk-test", max_retries=3, env={}, **kwargs)


def completion_body(text: str = "ok", usage=None) -> str:
    return json.dumps(
        {
            "choices": [
                {"message": {"content": text, "reasoning_content": "why"}, "finish_reason": "stop"}
            ],
            "usage": usage if usage is not None else {"prompt_tokens": 5, "completion_tokens": 7},
        }
    )


def test_complete_normalises_the_reply(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: FakeResponse(completion_body("你好")),
    )
    client = DeepSeekClient(_config())
    completion = client.complete([{"role": "user", "content": "hi"}])
    assert completion.text == "你好"
    assert completion.reasoning == "why"
    assert completion.usage["completion_tokens"] == 7
    assert completion.tokens == 7
    assert completion.finish_reason == "stop"
    assert client.exchanges[0]["status"] == 200
    assert client.exchanges[0]["request"]["body"]["model"] == "deepseek-chat"


def test_a_transient_status_is_retried_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def flaky(request, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {}, None)
        return FakeResponse(completion_body())

    monkeypatch.setattr("urllib.request.urlopen", flaky)
    client = DeepSeekClient(_config(), sleep=lambda seconds: None, random_uniform=lambda a, b: 0.0)
    assert client.complete([{"role": "user", "content": "hi"}]).text == "ok"
    assert calls["count"] == 2
    assert [exchange["status"] for exchange in client.exchanges] == [429, 200]


def test_a_client_error_is_not_retried(monkeypatch):
    calls = {"count": 0}

    def unauthorized(request, timeout=None):
        calls["count"] += 1
        raise urllib.error.HTTPError(request.full_url, 401, "bad key", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", unauthorized)
    client = DeepSeekClient(_config(), sleep=lambda seconds: None)
    with pytest.raises(LLMError, match="HTTP 401"):
        client.complete([{"role": "user", "content": "hi"}])
    assert calls["count"] == 1


def test_transport_errors_are_retried_up_to_the_budget(monkeypatch):
    calls = {"count": 0}

    def unreachable(request, timeout=None):
        calls["count"] += 1
        raise socket.timeout("timed out")

    monkeypatch.setattr("urllib.request.urlopen", unreachable)
    client = DeepSeekClient(_config(), sleep=lambda seconds: None, random_uniform=lambda a, b: 0.0)
    with pytest.raises(LLMError, match="giving up after 3 attempt"):
        client.complete([{"role": "user", "content": "hi"}])
    assert calls["count"] == 3
    assert all(exchange["error"] for exchange in client.exchanges)


def test_a_non_json_body_is_an_error_with_evidence(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout=None: FakeResponse("<html>502</html>")
    )
    client = DeepSeekClient(_config())
    with pytest.raises(LLMError, match="non-JSON response body"):
        client.complete([{"role": "user", "content": "hi"}])
    assert client.exchanges[0]["response_raw"] == "<html>502</html>"


def test_a_body_without_choices_is_an_error(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout=None: FakeResponse('{"usage": {}}')
    )
    client = DeepSeekClient(_config())
    with pytest.raises(LLMError, match="no choices"):
        client.complete([{"role": "user", "content": "hi"}])


def test_backoff_is_exponential_and_capped():
    client = DeepSeekClient(_config(), random_uniform=lambda a, b: 0.0)
    assert client._backoff(1) == 0.5
    assert client._backoff(3) == 2.0
    assert client._backoff(9) == 8.0


def test_list_models_reads_the_data_array(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: FakeResponse(
            '{"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}'
        ),
    )
    client = DeepSeekClient(_config())
    assert client.list_models() == ["deepseek-chat", "deepseek-reasoner"]


# ── the scripted client ───────────────────────────────────────────────────────
def test_scripted_client_replays_entries_in_order():
    client = ScriptedClient(
        ["first", {"content": "second", "usage": {"total_tokens": 3}}], label="unit"
    )
    assert client.complete([{"role": "user", "content": "a"}]).text == "first"
    second = client.complete([{"role": "user", "content": "b"}])
    assert second.text == "second"
    assert second.usage == {"total_tokens": 3}
    assert client.remaining == 0
    assert [call["index"] for call in client.calls] == [0, 1]
    assert client.prompts[0] == [{"role": "user", "content": "a"}]


def test_scripted_client_refuses_to_invent_output():
    client = ScriptedClient([], label="unit")
    with pytest.raises(LLMError, match="unit script exhausted"):
        client.complete([{"role": "user", "content": "a"}])


def test_scripted_client_records_a_wire_exchange():
    client = ScriptedClient(["hello"], label="unit")
    client.complete([{"role": "user", "content": "a"}])
    exchange = client.exchanges[0]
    assert exchange["endpoint"] == "scripted://unit"
    assert json.loads(exchange["response_raw"])["choices"][0]["message"]["content"] == "hello"
