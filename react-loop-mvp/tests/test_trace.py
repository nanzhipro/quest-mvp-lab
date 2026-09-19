"""trace.py and the JSONL evidence trail."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, List

import pytest

from react_loop_mvp.trace import ConsoleTracer, JsonlTracer, MultiTracer, Tracer


class TtyStream(io.StringIO):
    def isatty(self) -> bool:  # pretend to be a terminal
        return True


def sample_events(tracer: Tracer) -> None:
    tracer.record("task", task="算一下 1+1", mode="react", model="scripted")
    tracer.record("prompt", step=1, prompt="PROMPT BODY")
    tracer.record(
        "step",
        step=1,
        thought="直接算",
        action="calculator",
        action_input="1+1",
        observation="2",
        ok=True,
        latency_ms=12,
        usage={"prompt_tokens": 30, "completion_tokens": 5},
    )
    tracer.record("answer", answer="2", step=2)
    tracer.record(
        "stop",
        reason="final_answer",
        steps=1,
        elapsed_ms=99,
        usage_totals={"prompt_tokens": 30, "completion_tokens": 5},
    )


# ── JSONL ────────────────────────────────────────────────────────────────────
def test_jsonl_writes_one_object_per_line(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "trace.jsonl"
    tracer = JsonlTracer(path, run_id="run-1")
    sample_events(tracer)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in lines] == ["task", "prompt", "step", "answer", "stop"]
    assert all(entry["run_id"] == "run-1" for entry in lines)
    assert all(isinstance(entry["ts"], float) for entry in lines)
    assert lines[2]["observation"] == "2"
    assert tracer.records_written == 5


def test_jsonl_appends_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    JsonlTracer(path).record("task", task="a")
    JsonlTracer(path).record("task", task="b")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_jsonl_keeps_cjk_readable(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    JsonlTracer(path).record("task", task="中文问题")
    assert "中文问题" in path.read_text(encoding="utf-8")


# ── console ──────────────────────────────────────────────────────────────────
def test_console_renders_the_loop_without_colour() -> None:
    stream = io.StringIO()
    sample_events(ConsoleTracer(stream, color=False))
    rendered = stream.getvalue()
    assert "Thought: 直接算" in rendered
    assert "Action:" in rendered and "calculator" in rendered
    assert "Observation: 2" in rendered
    assert "Final Answer: 2" in rendered
    assert "stopped: final_answer" in rendered
    assert "\033[" not in rendered


def test_console_hides_the_prompt_unless_asked() -> None:
    plain = io.StringIO()
    sample_events(ConsoleTracer(plain, color=False))
    assert "PROMPT BODY" not in plain.getvalue()

    verbose = io.StringIO()
    sample_events(ConsoleTracer(verbose, color=False, show_prompt=True))
    assert "PROMPT BODY" in verbose.getvalue()
    assert "prompt @ step 1" in verbose.getvalue()


def test_console_colour_can_be_forced_and_disabled() -> None:
    forced = io.StringIO()
    ConsoleTracer(forced, color=True).record("answer", answer="x")
    assert "\033[1m" in forced.getvalue()

    quiet = io.StringIO()
    ConsoleTracer(quiet, color=False).record("answer", answer="x")
    assert "\033[" not in quiet.getvalue()


def test_no_color_environment_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TtyStream()
    ConsoleTracer(stream).record("answer", answer="x")
    assert "\033[" not in stream.getvalue()


def test_colour_is_off_when_the_stream_is_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = io.StringIO()
    ConsoleTracer(stream).record("answer", answer="x")
    assert "\033[" not in stream.getvalue()


def test_colour_is_on_for_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = TtyStream()
    ConsoleTracer(stream).record("answer", answer="x")
    assert "\033[" in stream.getvalue()


def test_console_wraps_long_observations_to_the_configured_width() -> None:
    stream = io.StringIO()
    tracer = ConsoleTracer(stream, color=False, width=60)
    tracer.record(
        "step",
        step=1,
        thought="",
        action="search_docs",
        action_input="q",
        observation="很长的观察 " * 40,
        ok=True,
        usage={},
        latency_ms=1,
    )
    body = [line for line in stream.getvalue().splitlines() if line and not line.startswith("  └")]
    assert all(len(line) <= 60 for line in body)
    assert len(body) > 1  # it really did wrap


def test_console_marks_tool_errors() -> None:
    stream = io.StringIO()
    ConsoleTracer(stream, color=False).record(
        "step",
        step=1,
        thought="",
        action="calculator",
        action_input="1/0",
        observation="tool 'calculator' failed",
        ok=False,
        usage={},
        latency_ms=1,
    )
    assert "Observation (tool error)" in stream.getvalue()


def test_console_shows_reasoning_only_on_request() -> None:
    stream = io.StringIO()
    ConsoleTracer(stream, color=False).record("reasoning", reasoning="心里想的话")
    assert stream.getvalue() == ""
    ConsoleTracer(stream, color=False, show_reasoning=True).record(
        "reasoning", reasoning="心里想的话"
    )
    assert "心里想的话" in stream.getvalue()


def test_console_ignores_unknown_events() -> None:
    stream = io.StringIO()
    ConsoleTracer(stream, color=False).record("native_step", step=1, calls=[])
    assert stream.getvalue() == ""


# ── composition ──────────────────────────────────────────────────────────────
def test_base_tracer_is_a_no_op() -> None:
    tracer = Tracer()
    tracer.record("anything", payload={"a": 1})
    tracer.close()


def test_multi_tracer_fans_out_and_closes(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    stream = io.StringIO()
    multi = MultiTracer([ConsoleTracer(stream, color=False), JsonlTracer(path)])
    sample_events(multi)
    multi.close()
    assert "Final Answer: 2" in stream.getvalue()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5


def test_multi_tracer_drops_missing_children() -> None:
    multi = MultiTracer([None, ConsoleTracer(io.StringIO(), color=False)])  # type: ignore[list-item]
    multi.record("task", task="x", mode="react", model="m")
    multi.close()


# ── end to end: a real run leaves a readable trail ───────────────────────────
def test_react_run_produces_a_complete_jsonl_trail(tmp_path: Path, registry: Any) -> None:
    from react_loop_mvp.llm import ScriptedClient
    from react_loop_mvp.react import ReActAgent

    path = tmp_path / "trace.jsonl"
    model = ScriptedClient(["Action: calculator\nAction Input: 2+2", "Final Answer: 4"])
    ReActAgent(model, registry, tracer=JsonlTracer(path, run_id="r")).run("2+2")
    events: List[dict] = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    kinds = [entry["event"] for entry in events]
    assert kinds == ["prompt", "step", "prompt", "answer", "stop"]
    assert events[0]["prompt"]  # the exact prompt of step 1 is on disk
    assert events[1]["observation"] == "4"
    assert events[-1]["usage_totals"] == {}
