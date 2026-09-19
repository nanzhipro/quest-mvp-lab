"""State-contract tests for the pure views every other module builds on.

The state dict is the only thing the graph threads between nodes, so its helper
functions (readiness, resumability, agent output lookup, citation collection, repair
targets) are tested directly: they are what the Supervisor's routing and the consistency
checker both stand on, and a subtle bug in any of them would show up as a wrong ruling
rather than a crash.
"""

from __future__ import annotations

from supervisor_graph_mvp import state as st


def _result(agent: str, *, ok: bool = True, status: str = "ok", **output):
    payload = {"status": status, "citations": [], "missing_inputs": [], **output}
    return {"subtask": "t", "agent": agent, "ok": ok, "output": payload}


def test_new_state_seeds_every_key(state_factory):
    run_state = st.new_state({"id": "x"}, {"mode": "test"})
    for key in (
        st.KEY_REQUEST,
        st.KEY_OPTIONS,
        st.KEY_INTENT,
        st.KEY_PLAN,
        st.KEY_RESULTS,
        st.KEY_FINDINGS,
        st.KEY_CONSISTENCY,
        st.KEY_RULING,
        st.KEY_NARRATIVE,
        st.KEY_VISITS,
        st.KEY_ARTIFACTS,
        st.KEY_PENDING_REPAIRS,
    ):
        assert key in run_state
    assert run_state[st.KEY_OPTIONS] == {"mode": "test"}
    assert run_state[st.KEY_REPAIRS] == 0


def test_reducers_are_registered_for_collections_only():
    assert set(st.REDUCERS) == {
        st.KEY_RESULTS,
        st.KEY_FINDINGS,
        st.KEY_REPAIR_LOG,
        st.KEY_LLM_CALLS,
        st.KEY_VISITS,
        st.KEY_ERRORS,
    }


def test_subtasks_and_result_lookup(state_factory):
    run_state = state_factory(plan={"subtasks": [{"id": "t1", "agent": "policy"}]})
    assert [task["id"] for task in st.subtasks(run_state)] == ["t1"]
    assert st.result_of(run_state, "t1") is None
    run_state[st.KEY_RESULTS] = {"t1": _result("policy")}
    assert st.result_of(run_state, "t1")["agent"] == "policy"


def test_agents_done_and_agent_names_differ_on_failure(state_factory):
    run_state = state_factory()
    run_state[st.KEY_RESULTS] = {"t1": _result("policy"), "t2": _result("evidence", ok=False)}
    assert st.agents_done(run_state) == ["policy"]
    assert st.agent_names(run_state) == ["evidence", "policy"]


def test_agent_output_prefers_the_latest_result(state_factory):
    run_state = state_factory()
    run_state[st.KEY_RESULTS] = {
        "t1": _result("policy", decision="review"),
        "t2": _result("policy", decision="block"),
    }
    assert st.agent_output(run_state, "policy")["decision"] == "block"
    assert st.outputs_by_agent(run_state)["policy"]["decision"] == "block"


def test_ready_subtasks_waits_for_successful_dependencies(state_factory):
    run_state = state_factory(
        plan={
            "subtasks": [
                {"id": "t1", "agent": "classification", "depends_on": []},
                {"id": "t2", "agent": "policy", "depends_on": ["t1"]},
            ]
        }
    )
    assert [task["id"] for task in st.ready_subtasks(run_state)] == ["t1"]
    run_state[st.KEY_RESULTS] = {"t1": _result("classification", ok=False)}
    assert [task["id"] for task in st.ready_subtasks(run_state)] == []
    run_state[st.KEY_RESULTS] = {"t1": _result("classification")}
    assert [task["id"] for task in st.ready_subtasks(run_state)] == ["t2"]


def test_pending_subtasks_lists_what_has_no_result(state_factory):
    run_state = state_factory(
        plan={
            "subtasks": [
                {"id": "t1", "agent": "a", "depends_on": []},
                {"id": "t2", "agent": "b", "depends_on": []},
            ]
        }
    )
    run_state[st.KEY_RESULTS] = {"t1": _result("a")}
    assert [task["id"] for task in st.pending_subtasks(run_state)] == ["t2"]


def test_resumable_subtasks_retries_incomplete_work_once_inputs_exist(state_factory):
    run_state = state_factory(
        plan={"subtasks": [{"id": "t1", "agent": "remediation", "depends_on": []}]}
    )
    run_state[st.KEY_RESULTS] = {
        "t1": _result("remediation", status="incomplete", missing_inputs=["policy"])
    }
    assert st.resumable_subtasks(run_state) == []
    run_state[st.KEY_RESULTS] = {
        "t1": _result("remediation", status="incomplete", missing_inputs=["policy"]),
        "t0": _result("policy"),
    }
    assert [task["id"] for task in st.resumable_subtasks(run_state)] == ["t1"]


def test_resumable_ignores_field_level_incompleteness(state_factory):
    run_state = state_factory(
        plan={"subtasks": [{"id": "t1", "agent": "remediation", "depends_on": []}]}
    )
    run_state[st.KEY_RESULTS] = {
        "t1": _result("remediation", status="incomplete", missing_fields=["actions"]),
        "t0": _result("policy"),
    }
    assert st.resumable_subtasks(run_state) == []


def test_dependency_outputs_collects_by_declared_id(state_factory):
    run_state = state_factory()
    run_state[st.KEY_RESULTS] = {"t1": _result("classification", level="P3")}
    task = {"id": "t2", "agent": "policy", "depends_on": ["t1", "t9"]}
    assert st.dependency_outputs(run_state, task) == {
        "t1": {"status": "ok", "citations": [], "missing_inputs": [], "level": "P3"}
    }


def test_known_citations_comes_from_the_artifact_index(state_factory):
    run_state = state_factory()
    run_state[st.KEY_ARTIFACTS] = {
        "artifact:a.csv": {"kind": "artifact"},
        "rule:R-01": {"kind": "rule"},
    }
    assert st.known_citations(run_state) == {"artifact:a.csv": "artifact", "rule:R-01": "rule"}


def test_citation_ids_are_deduplicated_in_first_seen_order(state_factory):
    run_state = state_factory()
    run_state[st.KEY_RESULTS] = {
        "t1": _result("classification", citations=[{"id": "artifact:a"}, {"id": "artifact:b"}]),
        "t2": _result("policy", citations=[{"id": "artifact:b"}, "rule:R-01"]),
    }
    assert st.citation_ids(run_state) == ["artifact:a", "artifact:b", "rule:R-01"]


def test_repair_targets_group_by_agent_and_keep_every_hint():
    findings = [
        {
            "rule": "C7-contract",
            "severity": "blocking",
            "message": "缺 policy",
            "repair": {"agent": "remediation", "goal": "重跑"},
        },
        {
            "rule": "C6-remediation",
            "severity": "blocking",
            "message": "处置为空",
            "repair": {"agent": "remediation", "goal": "重跑"},
        },
        {
            "rule": "C9-narrative",
            "severity": "warning",
            "message": "叙述无依据",
            "repair": {"agent": "remediation", "goal": "不该出现在这里"},
        },
    ]
    targets = st.repair_targets(findings)
    assert len(targets) == 1
    assert targets[0]["agent"] == "remediation"
    assert len(targets[0]["hints"]) == 2
    assert all("C9" not in hint for hint in targets[0]["hints"])


def test_repair_targets_ignore_findings_without_a_target():
    findings = [{"rule": "C1", "severity": "blocking", "message": "x", "repair": None}]
    assert st.repair_targets(findings) == []
