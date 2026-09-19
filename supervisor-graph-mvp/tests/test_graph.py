"""State-graph engine tests: build-time validation, reducers, routing, and the budgets.

The engine is the piece other projects would reuse, so it is tested without any
compliance domain involved: three nodes and a counter are enough to prove cycles,
reducers, conditional edges and the two stop conditions.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import pytest

from supervisor_graph_mvp.graph import (
    END,
    GraphError,
    StateGraph,
    append_reducer,
    merge_reducer,
    replace_reducer,
    validate_dag,
)


# ── reducers ──────────────────────────────────────────────────────────────────
def test_replace_reducer_takes_the_update():
    assert replace_reducer("old", "new") == "new"


def test_append_reducer_extends_and_tolerates_none():
    assert append_reducer(None, [1, 2]) == [1, 2]
    assert append_reducer([1], [2, 3]) == [1, 2, 3]


def test_merge_reducer_is_a_shallow_merge():
    assert merge_reducer({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert merge_reducer(None, {"b": 2}) == {"b": 2}


# ── build-time validation ─────────────────────────────────────────────────────
def _linear_graph(**kwargs: Any) -> StateGraph:
    graph = StateGraph("linear", **kwargs)
    graph.add_node("a", lambda state: {"counter": state.get("counter", 0) + 1})
    graph.add_node("b", lambda state: {"touched": True})
    graph.set_entry("a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    return graph


def test_duplicate_node_is_rejected():
    graph = StateGraph()
    graph.add_node("a", lambda state: {})
    with pytest.raises(GraphError, match="already defined"):
        graph.add_node("a", lambda state: {})


def test_reserved_end_name_is_rejected():
    with pytest.raises(GraphError, match="reserved"):
        StateGraph().add_node(END, lambda state: {})


def test_unknown_node_in_edge_is_rejected():
    graph = StateGraph()
    graph.add_node("a", lambda state: {})
    with pytest.raises(GraphError, match="unknown node"):
        graph.add_edge("a", "b")


def test_a_second_outgoing_edge_is_rejected():
    graph = StateGraph()
    graph.add_node("a", lambda state: {})
    graph.add_node("b", lambda state: {})
    graph.add_node("c", lambda state: {})
    graph.add_edge("a", "b")
    with pytest.raises(GraphError, match="already has an outgoing edge"):
        graph.add_edge("a", "c")
    with pytest.raises(GraphError, match="already has an outgoing edge"):
        graph.add_conditional_edges("a", lambda state: "b")


def test_conditional_mapping_targets_are_validated():
    graph = StateGraph()
    graph.add_node("a", lambda state: {})
    with pytest.raises(GraphError, match="unknown node"):
        graph.add_conditional_edges("a", lambda state: "x", {"x": "missing"})


def test_compile_requires_an_entry():
    graph = StateGraph()
    graph.add_node("a", lambda state: {})
    graph.add_edge("a", END)
    with pytest.raises(GraphError, match="no entry node"):
        graph.compile()


def test_compile_requires_an_outgoing_edge():
    graph = StateGraph()
    graph.add_node("a", lambda state: {})
    graph.set_entry("a")
    with pytest.raises(GraphError, match="no outgoing edge"):
        graph.compile()


def test_compile_rejects_unreachable_nodes():
    graph = StateGraph()
    graph.add_node("a", lambda state: {})
    graph.add_node("island", lambda state: {})
    graph.set_entry("a")
    graph.add_edge("a", END)
    graph.add_edge("island", END)
    with pytest.raises(GraphError, match="unreachable"):
        graph.compile()


def test_non_strict_mode_allows_unreachable_nodes():
    graph = StateGraph(strict=False)
    graph.add_node("a", lambda state: {})
    graph.add_node("island", lambda state: {})
    graph.set_entry("a")
    graph.add_edge("a", END)
    graph.add_edge("island", END)
    assert graph.compile() is not None


# ── invocation ────────────────────────────────────────────────────────────────
def test_linear_run_visits_each_node_once_and_stops_at_end():
    compiled = _linear_graph().compile()
    state = compiled.invoke({})
    assert state["counter"] == 1
    assert state["touched"] is True
    assert [visit["node"] for visit in state["visits"]] == ["a", "b"]
    assert state["graph"]["stopped_reason"] == "end"
    assert state["graph"]["steps"] == 2


def test_conditional_router_selects_the_branch():
    graph = StateGraph("branchy")
    graph.add_node("start", lambda state: {})
    graph.add_node("left", lambda state: {"side": "left"})
    graph.add_node("right", lambda state: {"side": "right"})
    graph.set_entry("start")
    graph.add_conditional_edges(
        "start", lambda state: state.get("want", "left"), {"left": "left", "right": "right"}
    )
    graph.add_edge("left", END)
    graph.add_edge("right", END)
    compiled = graph.compile()
    assert compiled.invoke({"want": "right"})["side"] == "right"
    assert compiled.invoke({"want": "left"})["side"] == "left"


def test_router_may_return_a_node_name_directly():
    graph = StateGraph("direct")
    graph.add_node("start", lambda state: {})
    graph.add_node("next", lambda state: {"done": True})
    graph.set_entry("start")
    graph.add_conditional_edges("start", lambda state: "next")
    graph.add_edge("next", END)
    assert graph.compile().invoke({})["done"] is True


def test_router_returning_an_unknown_target_raises():
    graph = StateGraph("bad-router")
    graph.add_node("start", lambda state: {})
    graph.add_node("next", lambda state: {})
    graph.set_entry("start")
    graph.add_conditional_edges("start", lambda state: "nowhere")
    graph.add_edge("next", END)
    with pytest.raises(GraphError, match="neither a mapped key"):
        graph.compile().invoke({})


def test_cycles_run_until_the_router_breaks_them():
    """The repair loop in miniature: go back while the counter is under the limit."""
    graph = StateGraph("loop")
    graph.add_node("work", lambda state: {"counter": state.get("counter", 0) + 1})
    graph.add_node("done", lambda state: {"finished": True})
    graph.set_entry("work")
    graph.add_conditional_edges(
        "work",
        lambda state: "again" if state.get("counter", 0) < 3 else "stop",
        {"again": "work", "stop": "done"},
    )
    graph.add_edge("done", END)
    state = graph.compile().invoke({})
    assert state["counter"] == 3
    assert state["finished"] is True
    assert state["graph"]["node_visits"]["work"] == 3


def test_visit_budget_stops_a_runaway_cycle():
    graph = StateGraph("runaway")
    graph.add_node("work", lambda state: {"counter": state.get("counter", 0) + 1})
    graph.set_entry("work")
    graph.add_conditional_edges("work", lambda state: "again", {"again": "work"})
    state = graph.compile(max_visits_per_node=4).invoke({})
    assert state["counter"] == 4
    assert state["graph"]["stopped_reason"] == "visit_budget:work"


def test_step_budget_is_recorded_when_it_truncates_a_run():
    graph = StateGraph("budget")
    graph.add_node("work", lambda state: {"counter": state.get("counter", 0) + 1})
    graph.add_node("done", lambda state: {})
    graph.set_entry("work")
    graph.add_conditional_edges(
        "work",
        lambda state: "again" if state.get("counter", 0) < 3 else "stop",
        {"again": "work", "stop": "done"},
    )
    graph.add_edge("done", END)
    state = graph.compile().invoke({}, max_steps=2)
    assert state["graph"]["steps"] == 2
    assert state["graph"]["stopped_reason"] == "step_budget"


def test_reducers_apply_per_key():
    graph = StateGraph("reducers", reducers={"items": append_reducer, "mapping": merge_reducer})
    graph.add_node("a", lambda state: {"items": ["x"], "mapping": {"a": 1}, "scalar": 1})
    graph.add_node("b", lambda state: {"items": ["y"], "mapping": {"b": 2}, "scalar": 2})
    graph.set_entry("a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    state = graph.compile().invoke({"items": [], "mapping": {}})
    assert state["items"] == ["x", "y"]
    assert state["mapping"] == {"a": 1, "b": 2}
    assert state["scalar"] == 2


def test_a_node_exception_is_recorded_and_the_run_stops_cleanly():
    def explode(state: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        raise RuntimeError("boom")

    graph = StateGraph("failing")
    graph.add_node("bad", explode)
    graph.set_entry("bad")
    graph.add_edge("bad", END)
    state = graph.compile().invoke({})
    assert state["visits"][0]["error"].startswith("RuntimeError: boom")
    assert state["visits"][0]["next_node"] == END


def test_node_events_are_emitted_to_the_listener():
    events = []
    compiled = _linear_graph().compile()
    compiled.invoke({}, on_event=lambda event, payload: events.append((event, payload.get("node"))))
    assert ("node_exit", "a") in events
    assert events[-1][0] == "graph_stop"


def test_describe_and_mermaid_expose_the_structure():
    graph = StateGraph("described")
    graph.add_node("a", lambda state: {}, description="入口")
    graph.add_node("b", lambda state: {}, description="收口")
    graph.set_entry("a")
    graph.add_conditional_edges("a", lambda state: "ok", {"ok": "b", "bad": END})
    graph.add_edge("b", END)
    compiled = graph.compile()
    rows = {row["node"]: row for row in compiled.describe()}
    assert rows["a"]["conditional"] is True
    assert rows["a"]["successors"] == ["b"]
    assert rows["b"]["description"] == "收口"
    mermaid = compiled.mermaid()
    assert mermaid.startswith("flowchart TD")
    assert "a -->|ok| b" in mermaid
    assert "DONE([end])" in mermaid


# ── DAG validation (pure, used by the Supervisor's planning duty) ─────────────
def test_valid_dag_has_no_problems():
    tasks = [
        {"id": "t1", "agent": "classification", "depends_on": []},
        {"id": "t2", "agent": "policy", "depends_on": ["t1"]},
    ]
    assert validate_dag(tasks, ["classification", "policy"]) == []


def test_duplicate_ids_are_reported():
    tasks = [
        {"id": "t1", "agent": "classification", "depends_on": []},
        {"id": "t1", "agent": "policy", "depends_on": []},
    ]
    problems = validate_dag(tasks, ["classification", "policy"])
    assert any("duplicate sub-task id" in problem for problem in problems)


def test_unknown_agent_is_reported_with_the_known_names():
    problems = validate_dag([{"id": "t1", "agent": "wizard", "depends_on": []}], ["classification"])
    assert any(
        "unknown agent 'wizard'" in problem and "classification" in problem for problem in problems
    )


def test_missing_dependency_is_reported():
    problems = validate_dag([{"id": "t1", "agent": "policy", "depends_on": ["t9"]}], ["policy"])
    assert any("missing 't9'" in problem for problem in problems)


def test_self_dependency_and_cycles_are_reported():
    self_dep = validate_dag([{"id": "t1", "agent": "policy", "depends_on": ["t1"]}], ["policy"])
    assert any("depends on itself" in problem for problem in self_dep)
    cyclic = validate_dag(
        [
            {"id": "t1", "agent": "policy", "depends_on": ["t2"]},
            {"id": "t2", "agent": "remediation", "depends_on": ["t1"]},
        ],
        ["policy", "remediation"],
    )
    assert any("dependency cycle" in problem for problem in cyclic)


def test_non_object_and_shapeless_entries_are_reported():
    problems = validate_dag(
        ["not-an-object", {"agent": "policy"}, {"id": "t1", "agent": "policy", "depends_on": "t9"}],
        ["policy"],
    )
    assert any("not an object" in problem for problem in problems)
    assert any("has no id" in problem for problem in problems)
    assert any("missing 't9'" in problem for problem in problems)
