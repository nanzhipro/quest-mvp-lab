"""预算护栏：五道闸各自的触发条件，以及"熔断后不再继续烧钱"的语义。"""

from __future__ import annotations

import pytest

from agent_guardrails.budget import Budget, BudgetExceeded, BudgetLimits


class FakeClock:
    """可推进的单调时钟 —— 测试不该真的等待。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_step_budget_trips_at_the_limit() -> None:
    budget = Budget(BudgetLimits(max_steps=2))
    budget.tick_step()
    budget.tick_step()
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.tick_step()
    assert excinfo.value.kind == "steps"
    assert "步数超限" in excinfo.value.detail


def test_token_budget_uses_model_reported_usage() -> None:
    budget = Budget(BudgetLimits(max_tokens=100))
    budget.tick_step(tokens=60)
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.tick_step(tokens=60)
    assert excinfo.value.kind == "tokens"
    assert excinfo.value.usage["tokens"] == 120


def test_wall_time_budget_uses_the_injected_clock() -> None:
    clock = FakeClock()
    budget = Budget(BudgetLimits(max_wall_time_s=10), clock=clock)
    budget.tick_step()
    clock.advance(11)
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.tick_step()
    assert excinfo.value.kind == "wall_time"


def test_tool_call_budget_trips_at_the_limit() -> None:
    budget = Budget(BudgetLimits(max_tool_calls=1))
    budget.tick_tool_call("read_file", {"path": "/workspace/a.txt"})
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.tick_tool_call("read_file", {"path": "/workspace/b.txt"})
    assert excinfo.value.kind == "tool_calls"


def test_loop_breaker_trips_on_identical_arguments() -> None:
    budget = Budget(BudgetLimits(loop_threshold=3))
    for _ in range(2):
        budget.tick_tool_call("web_fetch", {"url": "https://docs.example.com/timeout"})
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.tick_tool_call("web_fetch", {"url": "https://docs.example.com/timeout"})
    assert excinfo.value.kind == "loop"
    assert "死循环熔断" in excinfo.value.detail


def test_changing_arguments_resets_the_loop_window() -> None:
    """有效循环（参数在变）不该被误杀 —— 这正是"步数上限"做不到的事。"""
    budget = Budget(BudgetLimits(loop_threshold=3, max_tool_calls=10))
    for index in range(6):
        budget.tick_tool_call("read_file", {"path": "/workspace/{}.txt".format(index)})
    assert budget.usage()["tool_calls"] == 6
    assert budget.tripped is None


def test_usage_and_budget_line_are_human_readable() -> None:
    budget = Budget(BudgetLimits(max_steps=12, max_tokens=60_000))
    budget.tick_step(tokens=1200)
    budget.tick_tool_call("read_file", {"path": "/workspace/a.txt"})
    assert budget.usage() == {"steps": 1, "tool_calls": 1, "tokens": 1200, "elapsed_s": 0.0}
    assert budget.budget_line() == "step 1/12 · tools 1/16 · tokens 1.2k/60.0k · 0.0s/180s"


def test_tripped_kind_is_remembered_after_the_exception() -> None:
    budget = Budget(BudgetLimits(max_steps=1))
    budget.tick_step()
    with pytest.raises(BudgetExceeded):
        budget.tick_step()
    assert budget.tripped == "steps"
