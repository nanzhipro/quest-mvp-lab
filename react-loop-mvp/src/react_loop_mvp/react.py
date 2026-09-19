"""The ReAct loop — the whole point of the MVP, small enough to read in one sitting.

What a framework does for you is exactly two things: it *writes the prompt* and
it *parses the reply*. Everything else — the model client, the tools, the budget —
is ordinary code you would write anyway. This module makes that visible in about
twenty-five lines of loop, with zero magic: the prompt is a string that grows, the
reply is parsed with a regex, and the observation is the tool's return value.

The loop is deliberately *not* clever. Two guards keep it from burning money
(a repeated identical action, a reply that matches neither protocol), and that is
all: no planner, no memory, no retriever, no graph.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Dict, List, Optional

from .llm import ChatModel, Completion, Message
from .parsing import parse_reply
from .tools import ToolRegistry, ToolResult
from .trace import Tracer

SYSTEM_PROMPT = """你是一个 ReAct 智能体。三条硬约束：
1. 每轮回复只允许一个 Thought，外加最多一组 Action + Action Input。
2. 绝不自己写 Observation —— Observation 只由环境（工具）返回。
3. 事实已经足够时，直接输出 Final Answer，不要再调用工具。"""

TASK_TEMPLATE = """可用工具（每轮只能用一个）：
{tools}

输出格式（严格遵守，冒号后紧跟内容）：

Thought: <一句话推理>
Action: <工具名>
Action Input: <工具入参>

工具执行后会由环境返回一行 Observation，然后你继续输出下一个 Thought。
当你已经能回答问题时，改用：

Thought: 我已经收集到足够事实
Final Answer: <最终答案>

现在开始。

问题：{task}"""

PARSE_FAILURE_NUDGE = (
    "解析失败：回复必须包含唯一的 Action + Action Input，或包含 Final Answer。请重试。"
)
CONTINUATION = "\nThought:"


@dataclass(frozen=True)
class ReActStep:
    """One think → act → observe cycle, exactly as it appeared on the wire."""

    index: int
    thought: str
    action: Optional[str]
    action_input: Optional[str]
    observation: str
    ok: bool
    latency_ms: int = 0
    usage: Dict[str, int] = dc_field(default_factory=dict)
    prompt_chars: int = 0
    dropped_hallucinated_observation: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "step": self.index,
            "thought": self.thought,
            "action": self.action,
            "action_input": self.action_input,
            "observation": self.observation,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "prompt_chars": self.prompt_chars,
            "dropped_hallucinated_observation": self.dropped_hallucinated_observation,
        }


@dataclass(frozen=True)
class ReActResult:
    task: str
    answer: Optional[str]
    steps: List[ReActStep]
    stopped_reason: str
    elapsed_ms: int = 0
    usage_totals: Dict[str, int] = dc_field(default_factory=dict)
    last_text: str = ""
    last_step_prompt_chars: int = 0

    @property
    def tool_calls(self) -> int:
        return sum(1 for step in self.steps if step.action)

    @property
    def succeeded(self) -> bool:
        return self.stopped_reason == "final_answer"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": "react",
            "task": self.task,
            "answer": self.answer,
            "stopped_reason": self.stopped_reason,
            "steps": [step.as_dict() for step in self.steps],
            "tool_calls": self.tool_calls,
            "elapsed_ms": self.elapsed_ms,
            "usage_totals": self.usage_totals,
            "last_step_prompt_chars": self.last_step_prompt_chars,
        }


def build_prompt(task: str, catalog: str) -> str:
    """The entire 'framework' in one f-string: tools + rules + the question."""
    return TASK_TEMPLATE.format(tools=catalog, task=task.strip())


def to_messages(prompt: str) -> List[Message]:
    """ReAct keeps the whole trajectory in a single user message, on purpose."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def append_observation(prompt: str, reply_text: str, observation: str) -> str:
    """Grow the scratchpad: reply + Observation + a 'Thought:' nudge for the next turn."""
    return "{}{}\nObservation: {}{}".format(prompt, reply_text, observation, CONTINUATION)


class ReActAgent:
    """Text-protocol ReAct. Same model, same tools, zero framework."""

    def __init__(
        self,
        model: ChatModel,
        registry: ToolRegistry,
        *,
        max_steps: int = 8,
        tracer: Optional[Tracer] = None,
        repeat_limit: int = 3,
        parse_failure_limit: int = 3,
    ) -> None:
        self.model = model
        self.registry = registry
        self.max_steps = max_steps
        self.tracer = tracer or Tracer()
        self.repeat_limit = repeat_limit
        self.parse_failure_limit = parse_failure_limit

    # ─────────────────────────── core loop (start) ───────────────────────────
    def run(self, task: str) -> ReActResult:
        """Think → act → observe until a Final Answer, a budget stop, or a guard."""
        started = time.monotonic()
        prompt = build_prompt(task, self.registry.catalog())
        steps: List[ReActStep] = []
        previous, repeats = None, 0
        for index in range(1, self.max_steps + 1):
            self.tracer.record("prompt", step=index, prompt=prompt)
            reply = self.model.complete(to_messages(prompt))
            outcome = parse_reply(reply.text)
            if outcome.final_answer is not None:
                return self._result(
                    task, steps, "final_answer", started, reply, outcome.final_answer
                )
            if outcome.action is None:
                if self._reject(index, outcome, steps, reply, prompt) >= self.parse_failure_limit:
                    return self._result(task, steps, "unparsable", started, reply)
                prompt = append_observation(prompt, outcome.text, PARSE_FAILURE_NUDGE)
                continue
            key = (outcome.action.name, outcome.action.argument)
            repeats = repeats + 1 if key == previous else 0
            previous = key
            if repeats >= self.repeat_limit:
                return self._result(task, steps, "loop_detected", started, reply)
            result = self.registry.call_text(*key)
            steps.append(self._record(index, outcome, result, reply, prompt))
            prompt = append_observation(prompt, outcome.text, result.output)
        return self._result(task, steps, "max_steps", started)

    # ──────────────────────────── core loop (end) ────────────────────────────

    # ── helpers (the loop above stays readable because these live here) ───────
    def _record(
        self,
        index: int,
        outcome: Any,
        result: ToolResult,
        reply: Completion,
        prompt: str,
    ) -> ReActStep:
        """One executed action, recorded as a step and emitted to the tracer."""
        step = self._step(index, outcome, result.tool, result.output, result.ok, reply, prompt)
        self.tracer.record("step", **step.as_dict())
        return step

    def _reject(
        self,
        index: int,
        outcome: Any,
        steps: List[ReActStep],
        reply: Completion,
        prompt: str,
    ) -> int:
        """A reply that matched neither protocol: record it, return the failure streak."""
        steps.append(self._step(index, outcome, None, PARSE_FAILURE_NUDGE, False, reply, prompt))
        self.tracer.record("step", **steps[-1].as_dict())
        return complete_parse_failures(steps)

    def _step(
        self,
        index: int,
        outcome: Any,
        action: Optional[str],
        observation: str,
        ok: bool,
        reply: Completion,
        prompt: str,
    ) -> ReActStep:
        step = ReActStep(
            index=index,
            thought=outcome.thought,
            action=action,
            action_input=(outcome.action.argument if outcome.action else None),
            observation=observation,
            ok=ok,
            latency_ms=reply.latency_ms,
            usage=dict(reply.usage),
            prompt_chars=len(prompt),
            dropped_hallucinated_observation=outcome.hallucinated_observation,
        )
        if outcome.hallucinated_observation:
            self.tracer.record("note", message="dropped a model-written Observation")
        return step

    def _result(
        self,
        task: str,
        steps: List[ReActStep],
        reason: str,
        started: float,
        reply: Optional[Completion] = None,
        answer: Optional[str] = None,
    ) -> ReActResult:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        usage = merge_usage([step.usage for step in steps] + ([reply.usage] if reply else []))
        result = ReActResult(
            task=task,
            answer=answer,
            steps=list(steps),
            stopped_reason=reason,
            elapsed_ms=elapsed_ms,
            usage_totals=usage,
            last_text=(reply.text if reply else ""),
            last_step_prompt_chars=steps[-1].prompt_chars if steps else 0,
        )
        if answer is not None:
            self.tracer.record("answer", answer=answer, step=len(steps) + 1)
        self.tracer.record(
            "stop",
            reason=reason,
            steps=len(steps),
            elapsed_ms=elapsed_ms,
            usage_totals=usage,
            answer=answer,
        )
        return result


def complete_parse_failures(steps: List[ReActStep]) -> int:
    """How many *consecutive* unparsable replies closed the trajectory."""
    count = 0
    for step in reversed(steps):
        if step.action is None:
            count += 1
        else:
            break
    return count


def merge_usage(usages: List[Dict[str, int]]) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            totals[key] = totals.get(key, 0) + int(value)
    return totals


def result_json(result: ReActResult) -> str:
    """Pretty JSON of a finished trajectory — handy in the trace directory."""
    return json.dumps(result.as_dict(), ensure_ascii=False, indent=2)
