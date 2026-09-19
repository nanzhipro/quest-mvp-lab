"""native.py — the function-calling variant, asserted on the *protocol shape* it produces."""

from __future__ import annotations

from typing import Any, Dict, List

from react_loop_mvp.llm import Completion, ScriptedClient, ToolCall
from react_loop_mvp.native import (
    NativeAgent,
    assistant_message,
    call_id_of,
    tool_message,
)
from react_loop_mvp.tools import ToolRegistry
from react_loop_mvp.trace import Tracer


class RecordingTracer(Tracer):
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def record(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})


def call(name: str, arguments: str, call_id: str = "") -> Dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": arguments}


def test_call_id_falls_back_when_the_provider_omits_one() -> None:
    assert call_id_of(ToolCall(name="t"), 2, 0) == "call_2_0"
    assert call_id_of(ToolCall(name="t", call_id="abc"), 2, 0) == "abc"


def test_assistant_message_echoes_tool_calls_verbatim() -> None:
    reply = Completion(
        text="",
        tool_calls=[
            ToolCall(
                name="calculator",
                arguments={"expression": "1+1"},
                arguments_raw='{"expression": "1+1"}',
            )
        ],
    )
    message = assistant_message(reply, 1)
    assert message["role"] == "assistant"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"expression": "1+1"}'
    assert message["tool_calls"][0]["id"] == "call_1_0"


def test_assistant_message_without_tool_calls_is_plain_text() -> None:
    message = assistant_message(Completion(text="done"), 1)
    assert message == {"role": "assistant", "content": "done"}


def test_tool_message_matches_the_call_id() -> None:
    message = tool_message(ToolCall(name="calculator", call_id="c9"), 1, 0, "2")
    assert message == {"role": "tool", "tool_call_id": "c9", "name": "calculator", "content": "2"}


def test_two_hop_trajectory_returns_the_answer(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            {
                "content": "先检索。",
                "tool_calls": [call("search_docs", '{"query": "目录级 AUTH_OPEN"}', "c1")],
            },
            {
                "content": "取结论。",
                "tool_calls": [call("search_docs", '{"query": "SPEC.md §9 结论"}', "c2")],
            },
            {"content": "alpha/SPEC.md §9 通过验证。"},
        ]
    )
    tracer = RecordingTracer()
    result = NativeAgent(model, registry, tracer=tracer).run("alpha 的规范文档与结论？")

    assert result.succeeded
    assert result.tool_calls == 2
    assert result.steps[0].calls[0].arguments == {"query": "目录级 AUTH_OPEN"}
    assert result.answer == "alpha/SPEC.md §9 通过验证。"
    assert [entry["event"] for entry in tracer.events].count("step") == 2


def test_tools_schema_is_sent_on_every_call(registry: ToolRegistry) -> None:
    model = ScriptedClient([{"content": "直接回答"}])
    NativeAgent(model, registry).run("不用工具")
    assert model.calls[0]["tools"] is True


def test_messages_grow_with_assistant_and_tool_turns(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            {
                "content": "算一下",
                "tool_calls": [call("calculator", '{"expression": "6*7"}', "c1")],
            },
            {"content": "42"},
        ]
    )
    result = NativeAgent(model, registry).run("6*7")
    roles = [message["role"] for message in result.messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assert result.messages[2]["tool_calls"][0]["id"] == "c1"
    assert result.messages[3]["tool_call_id"] == "c1"
    assert result.messages[3]["content"] == "42"


def test_parallel_tool_calls_are_executed_in_one_step(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            {
                "content": "两个都要",
                "tool_calls": [
                    call("calculator", '{"expression": "2+2"}', "c1"),
                    call("calculator", '{"expression": "3+3"}', "c2"),
                ],
            },
            {"content": "4 和 6"},
        ]
    )
    result = NativeAgent(model, registry).run("并行调用")
    assert len(result.steps) == 1
    assert result.tool_calls == 2
    assert [record.observation for record in result.steps[0].calls] == ["4", "6"]


def test_unknown_tool_is_returned_as_a_tool_message(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            {"content": "", "tool_calls": [call("shell", '{"command": "ls"}', "c1")]},
            {"content": "改用 calculator。"},
        ]
    )
    result = NativeAgent(model, registry).run("未知工具")
    assert result.steps[0].calls[0].ok is False
    assert "unknown tool" in result.messages[3]["content"]


def test_repeated_identical_tool_calls_trip_the_guard(registry: ToolRegistry) -> None:
    reply = {"content": "", "tool_calls": [call("calculator", '{"expression": "1+1"}', "c1")]}
    model = ScriptedClient([reply] * 6)
    result = NativeAgent(model, registry, max_steps=6, repeat_limit=3).run("打转")
    assert result.stopped_reason == "loop_detected"
    assert len(result.steps) == 3


def test_budget_exhaustion_stops_without_an_answer(registry: ToolRegistry) -> None:
    reply = {"content": "", "tool_calls": [call("calculator", '{"expression": "1+1"}', "c1")]}
    model = ScriptedClient([reply] * 2)
    result = NativeAgent(model, registry, max_steps=2).run("预算")
    assert result.stopped_reason == "max_steps"
    assert result.answer is None
    assert result.succeeded is False


def test_usage_is_totalled_across_steps(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            {
                "content": "",
                "tool_calls": [call("calculator", '{"expression": "1+1"}', "c1")],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            },
            {"content": "2", "usage": {"prompt_tokens": 20, "completion_tokens": 2}},
        ]
    )
    result = NativeAgent(model, registry).run("1+1")
    assert result.usage_totals == {"prompt_tokens": 30, "completion_tokens": 3}
