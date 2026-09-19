"""The offline demo — the artifact a reader runs first, so it is tested hardest.

It must (a) drive both protocols to the same answer, (b) only use queries the bundled
corpus can actually answer, and (c) never pretend to have real token usage.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Dict, List

from react_loop_mvp.llm import ScriptedClient
from react_loop_mvp.native import NativeAgent
from react_loop_mvp.react import ReActAgent
from react_loop_mvp.tools import default_registry

EXPECTED_FACTS = ("es-mvp", "SPEC.md", "§9", "反向静音")


def script() -> Dict[str, Any]:
    raw = (
        resources.files("react_loop_mvp")
        .joinpath("data/demo_script.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


def react_run(task: str | None = None) -> Any:
    payload = script()
    model = ScriptedClient(payload["react"])
    return ReActAgent(model, default_registry()).run(task or payload["task"])


def native_run(task: str | None = None) -> Any:
    payload = script()
    model = ScriptedClient(payload["native"])
    return NativeAgent(model, default_registry()).run(task or payload["task"])


def test_react_demo_reaches_the_answer() -> None:
    result = react_run()
    assert result.succeeded
    assert result.stopped_reason == "final_answer"
    assert result.tool_calls == 2
    for fact in EXPECTED_FACTS:
        assert fact in (result.answer or "")


def test_native_demo_reaches_the_same_answer() -> None:
    result = native_run()
    assert result.succeeded
    assert result.tool_calls == 2
    for fact in EXPECTED_FACTS:
        assert fact in (result.answer or "")


def test_both_protocols_agree_on_the_substance() -> None:
    react_answer = react_run().answer or ""
    native_answer = native_run().answer or ""
    for fact in EXPECTED_FACTS:
        assert (fact in react_answer) and (fact in native_answer)


def test_demo_queries_are_all_answerable_from_the_bundled_corpus() -> None:
    """A demo whose first hop returns 'no document matched' would be a lie."""
    registry = default_registry()
    payload = script()
    queries: List[str] = []
    for reply in payload["react"]:
        for line in str(reply["content"]).splitlines():
            if line.lower().startswith("action input:"):
                queries.append(line.split(":", 1)[1].strip())
    for reply in payload["native"]:
        for call in reply.get("tool_calls", []):
            queries.append(json.loads(call["arguments"])["query"])
    assert len(queries) == 4
    for query in queries:
        observation = registry.call_text("search_docs", query).output
        assert "hit(s) of" in observation, query
        assert "no document matched" not in observation


def test_demo_records_no_fake_token_usage() -> None:
    payload = script()
    for section in ("react", "native"):
        for reply in payload[section]:
            assert "usage" not in reply


def test_demo_script_is_open_about_being_scripted() -> None:
    assert "Hand-authored" in script()["description"]
    assert "no token usage" in script()["description"].lower()


def test_demo_task_is_self_contained() -> None:
    payload = script()
    assert payload["task"].strip().endswith("？")
    assert "SPEC.md" not in payload["task"]  # the answer is not leaked in the question
