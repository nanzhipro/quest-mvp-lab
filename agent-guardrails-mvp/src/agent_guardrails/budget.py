"""预算护栏：为失控定价（步数 / 工具调用 / token / 时长 / 死循环五道闸）。

为什么"步数上限"不够：病态循环与有效循环的步数可以一样多，
真正能区分它们的是 **同参数重复率**。所以这里额外维护一个滑动窗口，
同一 ``(工具, 参数)`` 在窗口内重复出现即判定退化循环并熔断。

墙体时间用 ``time.monotonic()``（不受系统时钟调整影响），
并允许注入时钟 —— 测试因此不需要真的等待。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

# 生产常见取值区间：步数 10–50、单运行 token 5 万–50 万、重试 ≤3 次。
DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_TOOL_CALLS = 16
DEFAULT_MAX_TOKENS = 60_000
DEFAULT_MAX_WALL_TIME_S = 180.0
DEFAULT_LOOP_THRESHOLD = 3


class BudgetExceeded(RuntimeError):
    """预算熔断 —— 终止运行而不是重试，并带上足以定位的用量快照。"""

    def __init__(self, kind: str, detail: str, usage: Mapping[str, Any]) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.usage = dict(usage)

    def __str__(self) -> str:
        return "{}（用量 {}）".format(self.detail, self.usage)


@dataclass(frozen=True)
class BudgetLimits:
    """一次运行的硬上限。"""

    max_steps: int = DEFAULT_MAX_STEPS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_wall_time_s: float = DEFAULT_MAX_WALL_TIME_S
    loop_threshold: int = DEFAULT_LOOP_THRESHOLD

    def as_dict(self) -> Dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_tokens": self.max_tokens,
            "max_wall_time_s": self.max_wall_time_s,
            "loop_threshold": self.loop_threshold,
        }


def _signature(tool: str, args: Mapping[str, Any]) -> str:
    return "{}|{}".format(tool, json.dumps(dict(args), sort_keys=True, ensure_ascii=False, default=str))


class Budget:
    """一次运行的预算账本。每个动作前先记账（``tick_*``），超限即抛 :class:`BudgetExceeded`。"""

    def __init__(
        self,
        limits: Optional[BudgetLimits] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or BudgetLimits()
        self._clock = clock
        self._started = clock()
        self._steps = 0
        self._tool_calls = 0
        self._tokens = 0
        self._recent: List[str] = []
        self._tripped: Optional[str] = None

    # ── 记账 ──────────────────────────────────────────────────────────────────
    def tick_step(self, tokens: int = 0) -> Dict[str, Any]:
        """进入新一轮模型往返；``tokens`` 为上一轮实际消耗（真实 usage，不是估算）。"""
        self._steps += 1
        self._tokens += max(0, int(tokens))
        self._guard(
            "steps",
            self._steps > self.limits.max_steps,
            "步数超限：{} > {}".format(self._steps, self.limits.max_steps),
        )
        self._guard(
            "tokens",
            self._tokens > self.limits.max_tokens,
            "Token 预算超限：{} > {}".format(self._tokens, self.limits.max_tokens),
        )
        elapsed = self.elapsed_s
        self._guard(
            "wall_time",
            elapsed > self.limits.max_wall_time_s,
            "运行时长超限：{:.1f}s > {:.0f}s".format(elapsed, self.limits.max_wall_time_s),
        )
        return self.usage()

    def tick_tool_call(self, tool: str, args: Mapping[str, Any]) -> Dict[str, Any]:
        """工具调用前记账：次数上限 + 同参数重复（死循环）。"""
        self._tool_calls += 1
        self._guard(
            "tool_calls",
            self._tool_calls > self.limits.max_tool_calls,
            "工具调用数超限：{} > {}".format(self._tool_calls, self.limits.max_tool_calls),
        )
        window = max(1, self.limits.loop_threshold)
        self._recent = [*self._recent, _signature(tool, args)][-window:]
        if len(self._recent) == window and len(set(self._recent)) == 1:
            self._guard(
                "loop",
                True,
                "死循环熔断：{} 以相同参数连续调用 {} 次".format(tool, window),
            )
        return self.usage()

    def _guard(self, kind: str, tripped: bool, detail: str) -> None:
        if not tripped:
            return
        self._tripped = kind
        raise BudgetExceeded(kind, detail, self.usage())

    # ── 只读视图（日志与结果落盘用）────────────────────────────────────────────
    @property
    def elapsed_s(self) -> float:
        return max(0.0, self._clock() - self._started)

    @property
    def tripped(self) -> Optional[str]:
        return self._tripped

    def usage(self) -> Dict[str, Any]:
        return {
            "steps": self._steps,
            "tool_calls": self._tool_calls,
            "tokens": self._tokens,
            "elapsed_s": round(self.elapsed_s, 2),
        }

    def budget_line(self) -> str:
        """一行人类可读的余量，例如 ``step 3/12 · tools 2/16 · tokens 3.1k/60k · 18.4s/180s``。"""
        limits = self.limits
        return "step {}/{} · tools {}/{} · tokens {}/{} · {:.1f}s/{}s".format(
            self._steps,
            limits.max_steps,
            self._tool_calls,
            limits.max_tool_calls,
            _compact(self._tokens),
            _compact(limits.max_tokens),
            self.elapsed_s,
            int(limits.max_wall_time_s),
        )


def _compact(value: int) -> str:
    return "{:.1f}k".format(value / 1000.0) if value >= 1000 else str(value)


@dataclass
class Usage:
    """累计用量（给场景汇总用，避免调用方各自求和）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_calls: int = 0
    model_latency_ms: int = 0
    tool_calls: int = 0
    denials: int = 0
    approvals: int = 0
    redactions: Dict[str, int] = field(default_factory=dict)
