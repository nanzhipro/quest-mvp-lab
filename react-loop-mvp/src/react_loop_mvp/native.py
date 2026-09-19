"""The identical loop, written the way a framework writes it: native function calling.

Read this next to :mod:`react_loop_mvp.react` and the difference is embarrassingly
small. Both files use the same client, the same registry, the same step budget, the
same tracer, the same stop reasons. Only two things change:

* who writes the protocol — you (a template string) or the provider (a JSON schema);
* who parses the reply — you (``parsing.parse_reply``) or the provider (``tool_calls``).

Because the messages must be assembled correctly, this version is roughly twice as
long as the hand-written loop. That overhead *is* the framework: it is convenience,
not intelligence.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .llm import ChatModel, Completion, Message, ToolCall
from .react import merge_usage
from .tools import ToolRegistry
from .trace import Tracer

NATIVE_SYSTEM_PROMPT = """你是一个可以使用工具的智能体。三条硬约束：
1. 需要外部事实或计算时调用工具；同一轮可以并行调用多个工具。
2. 绝不臆造工具结果 —— 工具结果会以 role=tool 的消息返回给你。
3. 事实已经足够时，直接用自然语言给出最终答案，不要再调用工具。"""


def call_id_of(call: ToolCall, step: int, index: int) -> str:
    """Providers usually supply an id; tolerate the ones that do not."""
    return call.call_id or "call_{}_{}".format(step, index)


def assistant_message(reply: Completion, step: int) -> Message:
    """Echo the model's tool calls back verbatim — the protocol requires it."""
    message: Message = {"role": "assistant", "content": reply.text or ""}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": call_id_of(call, step, index),
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments_raw
                    or json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for index, call in enumerate(reply.tool_calls)
        ]
    return message


def tool_message(call: ToolCall, step: int, index: int, output: str) -> Message:
    return {
        "role": "tool",
        "tool_call_id": call_id_of(call, step, index),
        "name": call.name,
        "content": output,
    }


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    arguments: Dict[str, Any]
    observation: str
    ok: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.name,
            "arguments": self.arguments,
            "observation": self.observation,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class NativeStep:
    index: int
    text: str
    calls: List[ToolCallRecord] = dc_field(default_factory=list)
    latency_ms: int = 0
    usage: Dict[str, int] = dc_field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "step": self.index,
            "text": self.text,
            "calls": [call.as_dict() for call in self.calls],
            "latency_ms": self.latency_ms,
            "usage": self.usage,
        }


@dataclass(frozen=True)
class NativeResult:
    task: str
    answer: Optional[str]
    steps: List[NativeStep]
    stopped_reason: str
    messages: List[Message] = dc_field(default_factory=list)
    elapsed_ms: int = 0
    usage_totals: Dict[str, int] = dc_field(default_factory=dict)

    @property
    def tool_calls(self) -> int:
        return sum(len(step.calls) for step in self.steps)

    @property
    def succeeded(self) -> bool:
        return self.stopped_reason == "final_answer"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": "native",
            "task": self.task,
            "answer": self.answer,
            "stopped_reason": self.stopped_reason,
            "steps": [step.as_dict() for step in self.steps],
            "tool_calls": self.tool_calls,
            "elapsed_ms": self.elapsed_ms,
            "usage_totals": self.usage_totals,
            "messages": self.messages,
        }


class NativeAgent:
    """Function-calling variant: same tools, same budget, provider-side protocol."""

    def __init__(
        self,
        model: ChatModel,
        registry: ToolRegistry,
        *,
        max_steps: int = 8,
        tracer: Optional[Tracer] = None,
        repeat_limit: int = 3,
    ) -> None:
        self.model = model
        self.registry = registry
        self.max_steps = max_steps
        self.tracer = tracer or Tracer()
        self.repeat_limit = repeat_limit

    def run(self, task: str) -> NativeResult:
        started = time.monotonic()
        messages: List[Message] = [
            {"role": "system", "content": NATIVE_SYSTEM_PROMPT},
            {"role": "user", "content": task.strip()},
        ]
        steps: List[NativeStep] = []
        previous: Optional[Tuple[Tuple[str, str], ...]] = None
        repeats = 0
        for index in range(1, self.max_steps + 1):
            reply = self.model.complete(messages, tools=self.registry.json_schemas())
            self.tracer.record(
                "reply", step=index, text=reply.text, tool_calls=len(reply.tool_calls)
            )
            if not reply.tool_calls:
                return self._result(
                    task, steps, "final_answer", started, reply, reply.text, messages
                )
            key = tuple(sorted((call.name, call.arguments_raw) for call in reply.tool_calls))
            repeats = repeats + 1 if key == previous else 0
            previous = key
            if repeats >= self.repeat_limit:
                return self._result(task, steps, "loop_detected", started, reply, None, messages)
            messages.append(assistant_message(reply, index))
            records: List[ToolCallRecord] = []
            for position, call in enumerate(reply.tool_calls):
                result = self.registry.call_native(call.name, call.arguments)
                messages.append(tool_message(call, index, position, result.output))
                records.append(
                    ToolCallRecord(
                        name=call.name,
                        arguments=dict(call.arguments),
                        observation=result.output,
                        ok=result.ok,
                    )
                )
                self.tracer.record(
                    "step",
                    step=index,
                    thought=reply.text if position == 0 else "",
                    action=call.name,
                    action_input=call.arguments_raw
                    or json.dumps(call.arguments, ensure_ascii=False),
                    observation=result.output,
                    ok=result.ok,
                    latency_ms=reply.latency_ms if position == 0 else 0,
                    usage=dict(reply.usage) if position == 0 else {},
                )
            step = NativeStep(
                index=index,
                text=reply.text,
                calls=records,
                latency_ms=reply.latency_ms,
                usage=dict(reply.usage),
            )
            steps.append(step)
            self.tracer.record("native_step", **step.as_dict())
        return self._result(task, steps, "max_steps", started, None, None, messages)

    def _result(
        self,
        task: str,
        steps: List[NativeStep],
        reason: str,
        started: float,
        reply: Optional[Completion],
        answer: Optional[str],
        messages: Sequence[Message],
    ) -> NativeResult:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        usage = merge_usage([step.usage for step in steps] + ([reply.usage] if reply else []))
        result = NativeResult(
            task=task,
            answer=answer,
            steps=list(steps),
            stopped_reason=reason,
            messages=[dict(message) for message in messages],
            elapsed_ms=elapsed_ms,
            usage_totals=usage,
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
