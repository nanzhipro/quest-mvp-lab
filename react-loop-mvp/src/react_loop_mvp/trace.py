"""Tracing — the *evidence* half of the MVP.

Two renderers over the same event stream:

``JsonlTracer``
    One JSON object per line, including the exact prompt sent at each step and the
    raw model reply. This is the artifact you can diff, grep and hand to someone
    else; the console rendering is a convenience on top of it.
``ConsoleTracer``
    Human-readable trace (Thought / Action / Observation), colour only when the
    stream is a TTY and ``NO_COLOR`` is unset.

The loops emit events; they never print. That separation is what lets the same
code run headless in a test, in a cron job, or in a terminal.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Union

RESET = "\033[0m"
STYLES = {
    "dim": "\033[2m",
    "bold": "\033[1m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
}


class Tracer:
    """No-op base: loops only ever call :meth:`record`."""

    def record(self, event: str, **fields: Any) -> None:
        return None

    def close(self) -> None:
        return None


class MultiTracer(Tracer):
    """Fan-out tracer — typically ``MultiTracer([ConsoleTracer(), JsonlTracer(path)])``."""

    def __init__(self, tracers: List[Optional[Tracer]]) -> None:
        self._tracers = [tracer for tracer in tracers if tracer is not None]

    def record(self, event: str, **fields: Any) -> None:
        for tracer in self._tracers:
            tracer.record(event, **fields)

    def close(self) -> None:
        for tracer in self._tracers:
            tracer.close()


class JsonlTracer(Tracer):
    """Append-only JSONL. Reopened per write so a crash still leaves the trail."""

    def __init__(self, path: Union[str, Path], *, run_id: str = "") -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records_written = 0

    def record(self, event: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {"ts": round(time.time(), 3), "event": event}
        if self.run_id:
            payload["run_id"] = self.run_id
        payload.update(fields)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.records_written += 1


class ConsoleTracer(Tracer):
    """Render the loop for a human watching the terminal."""

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        color: Optional[bool] = None,
        show_prompt: bool = False,
        show_reasoning: bool = False,
        width: int = 100,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.show_prompt = show_prompt
        self.show_reasoning = show_reasoning
        self.width = width
        self.color = _color_enabled(self.stream) if color is None else color

    def record(self, event: str, **fields: Any) -> None:
        renderer = getattr(self, "_on_{}".format(event), None)
        if renderer is None:
            return
        renderer(fields)

    # ── per-event renderers ──────────────────────────────────────────────────
    def _on_task(self, fields: Dict[str, Any]) -> None:
        header = self._paint(
            "bold", "▶ {}/{}".format(fields.get("mode", "react"), fields.get("model", ""))
        )
        self._write("\n{} {}\n".format(header, self._fill(str(fields.get("task", "")))))

    def _on_prompt(self, fields: Dict[str, Any]) -> None:
        if not self.show_prompt:
            return
        body = str(fields.get("prompt", ""))
        self._write(
            self._paint(
                "dim", "┌─ prompt @ step {} ({} chars) ─────".format(fields.get("step"), len(body))
            )
            + "\n"
        )
        for line in body.splitlines():
            self._write(self._paint("dim", "│ {}".format(line)) + "\n")

    def _on_note(self, fields: Dict[str, Any]) -> None:
        self._write(self._paint("yellow", "! {}".format(fields.get("message", ""))) + "\n")

    def _on_reasoning(self, fields: Dict[str, Any]) -> None:
        if not self.show_reasoning:
            return
        text = str(fields.get("reasoning", "")).strip()
        if text:
            self._write(self._paint("dim", self._fill("  reasoning: " + text)) + "\n")

    def _on_reply(self, fields: Dict[str, Any]) -> None:
        if fields.get("text"):
            self._write(self._paint("cyan", self._fill("» " + str(fields["text"]).strip())) + "\n")

    def _on_step(self, fields: Dict[str, Any]) -> None:
        thought = str(fields.get("thought", "")).strip()
        if thought:
            self._write(self._paint("cyan", self._fill("Thought: " + thought)) + "\n")
        action = fields.get("action")
        if action:
            self._write(
                self._paint(
                    "green",
                    self._fill(
                        "Action: {0} (input: {1})".format(action, fields.get("action_input", ""))
                    ),
                )
                + "\n"
            )
        ok = fields.get("ok", True)
        marker = "Observation:" if ok else "Observation (tool error):"
        block = self._fill("{} {}".format(marker, fields.get("observation", "")))
        self._write((block if ok else self._paint("red", block)) + "\n")
        usage = fields.get("usage") or {}
        if usage or fields.get("latency_ms"):
            self._write(
                self._paint(
                    "dim",
                    "  └ step {} · {} ms · tokens {}/{}".format(
                        fields.get("step"),
                        fields.get("latency_ms", 0),
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                    ),
                )
                + "\n"
            )

    def _on_answer(self, fields: Dict[str, Any]) -> None:
        self._write(
            "\n{} {}\n".format(
                self._paint("bold", "Final Answer:"), self._fill(str(fields.get("answer", "")))
            )
        )

    def _on_stop(self, fields: Dict[str, Any]) -> None:
        reason = fields.get("reason")
        style = "green" if reason == "final_answer" else "yellow"
        self._write(
            self._paint(
                style,
                "─ stopped: {} · {} step(s) · {} ms · prompt_tokens {}".format(
                    reason,
                    fields.get("steps", 0),
                    fields.get("elapsed_ms", 0),
                    (fields.get("usage_totals") or {}).get("prompt_tokens", 0),
                ),
            )
            + "\n"
        )

    def _on_error(self, fields: Dict[str, Any]) -> None:
        self._write(self._paint("red", "× {}".format(fields.get("message", ""))) + "\n")

    # ── helpers ──────────────────────────────────────────────────────────────
    def _write(self, text: str) -> None:
        self.stream.write(text)

    def _paint(self, style: str, text: str) -> str:
        if not self.color:
            return text
        return "{}{}{}".format(STYLES.get(style, ""), text, RESET)

    def _fill(self, text: str) -> str:
        """Wrap to the terminal width; continuation lines are indented two spaces."""
        collapsed = text if "\n" not in text else text.replace("\n", " ")
        return textwrap.fill(collapsed, width=self.width, subsequent_indent="  ")


def _color_enabled(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())
