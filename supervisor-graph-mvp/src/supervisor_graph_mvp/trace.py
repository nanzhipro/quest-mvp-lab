"""Evidence capture: a console log a human can follow, a JSONL trail a script can replay.

Three artifacts per run, same convention as the sibling MVPs:

``trace.jsonl``
    Append-only event stream — every node exit, routing decision, sub-task execution,
    consistency verdict and model call. Written as it happens, so an interrupted run
    still leaves a readable trail.
``wire.jsonl``
    Every raw model request/response pair, straight from the client.
``result.json``
    The final state (see :func:`reporting.result_payload`).

The console stream is not decoration: it is the cheapest way to see the Supervisor's
four duties happening in order, and the reason ``--trace-dir`` is optional while the
log is not.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, TextIO

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
COLORS = {
    "run_start": "\033[36m",
    "node_exit": "",
    "intent": "\033[35m",
    "plan": "\033[35m",
    "subtask": "\033[34m",
    "consistency": "\033[33m",
    "ruling": "\033[32m",
    "escalation": "\033[31m",
    "graph_stop": "\033[36m",
    "node_error": "\033[31m",
}


def make_run_id(label: str = "run") -> str:
    """``<YYYYmmdd_HHMMSS>_<label>`` — one directory per run, by convention."""
    return "{}_{}".format(time.strftime("%Y%m%d_%H%M%S"), label)


@dataclass
class Tracer:
    """Collects events, writes the evidence files, and narrates progress to a stream."""

    run_id: str = ""
    trace_path: Optional[Path] = None
    wire_path: Optional[Path] = None
    result_path: Optional[Path] = None
    console: bool = True
    stream: Optional[TextIO] = None
    color: Optional[bool] = None
    events: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = make_run_id()
        if self.stream is None:
            self.stream = sys.stderr
        if self.color is None:
            self.color = bool(
                getattr(self.stream, "isatty", lambda: False)()
            ) and not os.environ.get("NO_COLOR")
        for path in (self.trace_path, self.wire_path, self.result_path):
            if path is not None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)

    # ── events ────────────────────────────────────────────────────────────────
    def emit(self, event: str, payload: Mapping[str, Any]) -> None:
        """Record one event: append to the trail, write the line, narrate it."""
        record = {"ts": time.time(), "run_id": self.run_id, "event": event, **dict(payload)}
        self.events.append(record)
        if self.trace_path is not None:
            with Path(self.trace_path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if self.console:
            self._narrate(event, record)

    def _narrate(self, event: str, record: Mapping[str, Any]) -> None:
        line = self._line_for(event, record)
        if not line:
            return
        prefix = COLORS.get(event, "")
        suffix = RESET if prefix else ""
        print("{}{}{}".format(prefix, line, suffix), file=self.stream)

    def _line_for(self, event: str, record: Mapping[str, Any]) -> str:
        if event == "run_start":
            return "▶ 运行 {} · 图 {}".format(
                self.run_id, (record.get("request") or {}).get("id", "?")
            )
        if event == "node_exit":
            arrow = "→ {}".format(record.get("next_node")) if record.get("next_node") else ""
            error = "  ✗ {}".format(record.get("error")) if record.get("error") else ""
            return "  · {:<9} {}ms {} {}".format(
                record.get("node"), record.get("duration_ms"), arrow, error
            ).rstrip()
        if event == "node_error":
            return "  ✗ {} 失败：{}".format(record.get("node"), record.get("error"))
        if event == "intent":
            intent = record.get("intent") or {}
            return "  意图 {:<22} 来源 {}{}  {}".format(
                ", ".join(intent.get("intents") or []) or "-",
                intent.get("source"),
                "(composite)" if intent.get("composite") else "",
                intent.get("reason") or "",
            ).rstrip()
        if event == "plan":
            plan = record.get("plan") or {}
            tasks = " ".join(
                "{}({})".format(task.get("id"), task.get("agent"))
                for task in plan.get("subtasks") or []
            )
            return "  计划 [{}] 来源 {}  {}".format(
                tasks, plan.get("source"), plan.get("rationale") or ""
            ).rstrip()
        if event == "subtask":
            return "  派发 {} → {:<14} {} {}ms{}".format(
                record.get("subtask"),
                record.get("agent"),
                record.get("status"),
                record.get("duration_ms"),
                "  ↻修复" if record.get("hint") else "",
            )
        if event == "consistency":
            return "  一致性 {} · 阻断 {} · 告警 {} → {}".format(
                "通过" if record.get("passed") else "未通过",
                record.get("blocking"),
                record.get("warnings"),
                record.get("decision"),
            )
        if event == "ruling":
            return "  裁决 {}".format((record.get("ruling") or {}).get("verdict"))
        if event == "escalation":
            return "  升级人工：{} 项阻断问题无法在预算内收口".format(record.get("blocking"))
        if event == "graph_stop":
            return "■ 结束：{} 步 · {} 个节点访问 · {}".format(
                record.get("steps"), record.get("visits"), record.get("stopped_reason")
            )
        return ""

    # ── files ─────────────────────────────────────────────────────────────────
    def write_wire(self, exchanges: List[Mapping[str, Any]]) -> Optional[Path]:
        if self.wire_path is None:
            return None
        with Path(self.wire_path).open("w", encoding="utf-8") as handle:
            for index, exchange in enumerate(exchanges):
                handle.write(
                    json.dumps(
                        {"run_id": self.run_id, "index": index, **dict(exchange)},
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
        return Path(self.wire_path)

    def write_result(self, payload: Mapping[str, Any]) -> Optional[Path]:
        if self.result_path is None:
            return None
        Path(self.result_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return Path(self.result_path)

    def say(self, message: str) -> None:
        """A free-form progress line that is not an event (e.g. ``wrote <path>``)."""
        if self.console:
            print(message, file=self.stream)


def trace_dir(base: Path, run_id: str, mode: str = "") -> Path:
    """``<base>/<run_id>[_mode]/`` — one clean directory per run, never reused."""
    name = "{}_{}".format(run_id, mode) if mode else run_id
    path = Path(base) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["Tracer", "make_run_id", "trace_dir"]
