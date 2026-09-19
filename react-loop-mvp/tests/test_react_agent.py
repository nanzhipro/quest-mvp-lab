"""react.py — the hand-written loop: growth, guards, stopping, and the trace it leaves."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from react_loop_mvp.llm import ScriptedClient
from react_loop_mvp.react import (
    CONTINUATION,
    ReActAgent,
    append_observation,
    build_prompt,
    complete_parse_failures,
    merge_usage,
    result_json,
    to_messages,
)
from react_loop_mvp.tools import ToolRegistry
from react_loop_mvp.trace import Tracer


class RecordingTracer(Tracer):
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def record(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})

    def kinds(self) -> List[str]:
        return [entry["event"] for entry in self.events]


def test_prompt_contains_the_tools_and_the_question(registry: ToolRegistry) -> None:
    prompt = build_prompt("算一下 1+1", registry.catalog())
    assert "calculator[expression]" in prompt
    assert "search_docs[query]" in prompt
    assert prompt.rstrip().endswith("算一下 1+1")
    assert "Final Answer" in prompt


def test_react_keeps_the_trajectory_in_a_single_user_message() -> None:
    messages = to_messages("prompt body")
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1]["content"] == "prompt body"


def test_append_observation_grows_the_scratchpad_and_nudges_a_thought() -> None:
    grown = append_observation("P", "Thought: t\nAction: calculator\nAction Input: 1+1", "2")
    assert grown.startswith("PThought: t")
    assert "\nObservation: 2" in grown
    assert grown.endswith(CONTINUATION)


def test_two_hop_trajectory_produces_the_final_answer(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            "Thought: 先检索。\nAction: search_docs\nAction Input: 目录级 AUTH_OPEN\n",
            "Thought: 去取规范结论。\nAction: search_docs\nAction Input: SPEC.md §9 结论\n",
            "Thought: 事实齐了。\nFinal Answer: alpha/SPEC.md §9 通过验证。",
        ]
    )
    tracer = RecordingTracer()
    result = ReActAgent(model, registry, tracer=tracer).run("alpha 的规范文档与结论？")

    assert result.succeeded
    assert result.stopped_reason == "final_answer"
    assert result.tool_calls == 2
    assert result.steps[0].observation.startswith("1 hit(s) of 4 documents:")
    assert len(result.steps) == 2
    assert "alpha/SPEC.md" in (result.answer or "")
    assert result.elapsed_ms >= 0
    assert tracer.kinds().count("step") == 2
    assert "answer" in tracer.kinds() and "stop" in tracer.kinds()


def test_the_scratchpad_actually_grows_between_calls(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            "Action: calculator\nAction Input: 2+2",
            "Final Answer: 4",
        ]
    )
    ReActAgent(model, registry).run("2+2 等于多少？")
    first, second = (call[1]["content"] for call in model.prompts)
    assert "Observation: 4" not in model.prompts[0][1]["content"]  # first turn: no observation yet
    assert "Observation: 4" in second
    assert len(second) > len(first)


def test_react_records_usage_per_step_and_in_total(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            {
                "content": "Action: calculator\nAction Input: 1+1",
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
            {"content": "Final Answer: 2", "usage": {"prompt_tokens": 150, "completion_tokens": 8}},
        ]
    )
    result = ReActAgent(model, registry).run("1+1")
    assert result.steps[0].usage["prompt_tokens"] == 100
    assert result.usage_totals == {"prompt_tokens": 250, "completion_tokens": 18}


def test_unknown_tool_becomes_an_observation_instead_of_a_crash(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            "Action: shell\nAction Input: rm -rf /",
            "Final Answer: 我改用 calculator。",
        ]
    )
    result = ReActAgent(model, registry).run("试试未知工具")
    assert result.steps[0].ok is False
    assert "unknown tool 'shell'" in result.steps[0].observation
    assert result.succeeded


def test_budget_exhaustion_stops_with_max_steps(registry: ToolRegistry) -> None:
    model = ScriptedClient(["Action: calculator\nAction Input: 1+1"] * 3)
    result = ReActAgent(model, registry, max_steps=3).run("永不收敛的问题")
    assert result.stopped_reason == "max_steps"
    assert result.answer is None
    assert len(result.steps) == 3
    assert result.succeeded is False


def test_repeated_identical_action_trips_the_loop_guard(registry: ToolRegistry) -> None:
    """Three identical consecutive actions are allowed; the fourth is refused."""
    model = ScriptedClient(["Action: calculator\nAction Input: 1+1"] * 6)
    result = ReActAgent(model, registry, max_steps=6, repeat_limit=3).run("打转")
    assert result.stopped_reason == "loop_detected"
    assert len(result.steps) == 3
    assert model.remaining == 2  # the model was called a 4th time, the tool was not


def test_repeat_counter_resets_when_the_action_changes(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            "Action: calculator\nAction Input: 1+1",
            "Action: calculator\nAction Input: 2+2",
            "Action: calculator\nAction Input: 1+1",
            "Final Answer: 2",
        ]
    )
    result = ReActAgent(model, registry, max_steps=6, repeat_limit=3).run("交替动作")
    assert result.succeeded
    assert result.tool_calls == 3


def test_unparsable_replies_are_nudged_then_stopped(registry: ToolRegistry) -> None:
    model = ScriptedClient(["我觉得应该先查一下文档"] * 3)
    result = ReActAgent(model, registry, max_steps=6, parse_failure_limit=3).run("格式不合规")
    assert result.stopped_reason == "unparsable"
    assert len(result.steps) == 3
    assert model.prompts[1][1]["content"].rstrip().endswith("Thought:")
    assert "解析失败" in model.prompts[1][1]["content"]


def test_a_single_unparsable_reply_is_recoverable(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            "我先想一想……",
            "Action: calculator\nAction Input: 3*3",
            "Final Answer: 9",
        ]
    )
    result = ReActAgent(model, registry, max_steps=5).run("3*3")
    assert result.succeeded
    assert result.steps[0].action is None
    assert result.steps[1].observation == "9"


def test_action_without_input_is_reported_as_a_tool_error(registry: ToolRegistry) -> None:
    model = ScriptedClient(["Action: calculator\n", "Final Answer: 放弃了"])
    result = ReActAgent(model, registry).run("缺入参")
    assert result.steps[0].ok is False
    assert "Action Input" in result.steps[0].observation


def test_model_written_observation_is_dropped_and_flagged(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            "Action: calculator\nAction Input: 1+1\nObservation: 3",
            "Final Answer: 2",
        ]
    )
    result = ReActAgent(model, registry).run("幻觉观察")
    assert result.steps[0].dropped_hallucinated_observation is True
    # the real tool ran, and its (correct) output is what the model sees next
    assert "Observation: 2" in model.prompts[1][1]["content"]
    assert "Observation: 3" not in model.prompts[1][1]["content"]


def test_complete_parse_failures_counts_only_the_tail(registry: ToolRegistry) -> None:
    model = ScriptedClient(
        [
            "乱写",
            "Action: calculator\nAction Input: 1+1",
            "乱写",
            "乱写",
            "Final Answer: 2",
        ]
    )
    agent = ReActAgent(model, registry, max_steps=6, parse_failure_limit=3)
    result = agent.run("混合轨迹")
    assert result.succeeded
    assert complete_parse_failures(result.steps) == 2  # the two before the answer


def test_result_json_is_stable_and_complete(registry: ToolRegistry) -> None:
    model = ScriptedClient(["Action: calculator\nAction Input: 2+2", "Final Answer: 4"])
    result = ReActAgent(model, registry).run("2+2")
    payload = result_json(result)
    assert '"mode": "react"' in payload
    assert '"stopped_reason": "final_answer"' in payload
    assert '"tool_calls": 1' in payload


def test_merge_usage_sums_missing_keys_safely() -> None:
    assert merge_usage([{"a": 1}, {}, {"a": 2, "b": 3}]) == {"a": 3, "b": 3}
    assert merge_usage([]) == {}


def test_agent_defaults_to_a_no_op_tracer(registry: ToolRegistry) -> None:
    result = ReActAgent(ScriptedClient(["Final Answer: 马上回答"]), registry).run("直接答")
    assert result.answer == "马上回答"
    assert result.steps == []


@pytest.mark.parametrize("max_steps", [1, 2, 5])
def test_step_budget_is_respected(registry: ToolRegistry, max_steps: int) -> None:
    model = ScriptedClient(["Action: calculator\nAction Input: 1+1"] * max_steps)
    result = ReActAgent(model, registry, max_steps=max_steps).run("预算")
    assert len(result.steps) <= max_steps
