"""日志：上下文的绑定与还原、JSONL 的字段完备性、两路渲染的差异。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agent_guardrails import obs
from conftest import events_named, read_events


def test_context_is_bound_and_restored() -> None:
    assert obs.current_context()["session"] == "-"
    with obs.bind_context(session="s1", step=1):
        assert obs.current_context()["session"] == "s1"
        with obs.bind_context(step=2):
            assert obs.current_context()["step"] == 2
        assert obs.current_context()["step"] == 1
    assert obs.current_context()["session"] == "-"


def test_unknown_context_key_is_rejected() -> None:
    with pytest.raises(KeyError), obs.bind_context(trace_id="nope"):
        pass


def test_every_event_carries_the_context_coordinates(log_file: Path) -> None:
    with obs.bind_context(session="abc123", scenario="happy_path", step=2, tool="read_file"):
        obs.event("gate.decision", "放行", decision="allow", rule="gate.risk_low")
    record = events_named(log_file, "gate.decision")[0]
    assert (record["session"], record["scenario"], record["step"], record["tool"]) == (
        "abc123",
        "happy_path",
        2,
        "read_file",
    )
    assert record["decision"] == "allow"
    assert record["rule"] == "gate.risk_low"
    assert record["ts"].endswith("+08:00") or "T" in record["ts"]
    assert set(record) >= {"ts", "level", "event", "message", "session", "scenario", "step", "tool"}


def test_debug_events_are_filtered_by_level(tmp_path: Path) -> None:
    path = tmp_path / "info.jsonl"
    obs.configure_logging(level="INFO", console=False, jsonl_path=path)
    try:
        obs.event("loop.step", "细节", level=logging.DEBUG)
        obs.event("gate.decision", "结论")
    finally:
        obs.configure_logging(level="INFO", console=False)
    assert [event["event"] for event in read_events(path)] == ["gate.decision"]


def test_message_field_is_optional_and_event_name_is_used_by_default() -> None:
    logger = logging.getLogger(obs.LOGGER_NAME)
    records = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    handler.addFilter(obs._ContextFilter())
    logger.addHandler(handler)
    try:
        obs.event("budget.trip")
    finally:
        logger.removeHandler(handler)
    assert records[0].getMessage() == "budget.trip"


def test_console_rendering_fits_one_line_and_shows_context_and_fields() -> None:
    formatter = obs.ConsoleFormatter(color=False)
    record = logging.LogRecord("agent_guardrails", logging.WARNING, "f", 1, "参数未通过校验", (), None)
    record.event = "gate.decision"
    record.fields = {"tool": "read_file", "rule": "gate.arg_rule", "args": {"path": "/etc/passwd"}}
    record.session = "a1b2c3"
    record.scenario = "privilege_escalation"
    record.step = 2
    record.tool = "read_file"
    line = formatter.format(record)
    assert "\n" not in line
    assert "WARNING" in line and "gate.decision" in line
    assert "session=a1b2c3" in line and "step=2" in line
    assert "tool=read_file" in line and "rule=gate.arg_rule" in line


def test_console_truncates_long_values_but_jsonl_keeps_them(tmp_path: Path) -> None:
    formatter = obs.ConsoleFormatter(color=False)
    long_text = "甲" * 400
    record = logging.LogRecord("agent_guardrails", logging.INFO, "f", 1, "长字段", (), None)
    record.event = "tool.exec"
    record.fields = {"output": long_text}
    for key in obs.CONTEXT_KEYS:
        setattr(record, key, "-")
    rendered = formatter.format(record)
    assert "…" in rendered and len(rendered) < 400

    path = tmp_path / "long.jsonl"
    obs.configure_logging(level="DEBUG", console=False, jsonl_path=path)
    try:
        obs.event("tool.exec", output=long_text)
    finally:
        obs.configure_logging(level="INFO", console=False)
    assert read_events(path)[0]["output"] == long_text


def test_structured_values_survive_the_json_round_trip(log_file: Path) -> None:
    obs.event("gate.decision", args={"to": "a@b.c"}, hits=["x", "y"], ok=True, count=3)
    record = events_named(log_file, "gate.decision")[0]
    assert record["args"] == {"to": "a@b.c"}
    assert record["hits"] == ["x", "y"]
    assert record["ok"] is True
    assert record["count"] == 3
    assert json.dumps(record)  # 每个事件都可序列化
