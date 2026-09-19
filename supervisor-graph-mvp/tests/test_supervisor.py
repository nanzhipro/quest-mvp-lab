"""Supervisor tests — the four duties, each with its deterministic fallback.

These tests never touch the network: the model is a :class:`ScriptedClient` replaying
replies a real model could plausibly produce, including the two that are wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from supervisor_graph_mvp.llm import ScriptedClient
from supervisor_graph_mvp.supervisor import (
    DEFAULT_INTENT,
    INTENTS,
    Supervisor,
    citations_in,
    deterministic_narrative,
    template_plan,
    unknown_citations,
)


def make_supervisor(sandbox: Path, script: List[Any]) -> Supervisor:
    from supervisor_graph_mvp.agents.registry import default_registry

    return Supervisor(ScriptedClient(script, label="unit"), default_registry(sandbox))


def classify_json(intents: List[str], primary: Optional[str] = None, reason: str = "因为") -> str:
    return json.dumps(
        {
            "intents": intents,
            "primary": primary or (intents[0] if intents else ""),
            "reason": reason,
        },
        ensure_ascii=False,
    )


# ── duty 1: intent recognition ────────────────────────────────────────────────
def test_classify_reads_the_model_reply(sandbox, request_factory):
    supervisor = make_supervisor(
        sandbox,
        [classify_json(["data_classification", "policy_applicability"], "data_classification")],
    )
    intent = supervisor.classify(request_factory())
    assert intent["source"] == "llm"
    assert intent["intents"] == ["data_classification", "policy_applicability"]
    assert intent["primary"] == "data_classification"
    assert intent["composite"] is True
    assert intent["llm"]["parsed"] is True


def test_classify_falls_back_to_keywords_when_the_reply_is_not_json(sandbox, request_factory):
    supervisor = make_supervisor(sandbox, ["我不知道该怎么分类"])
    intent = supervisor.classify(request_factory(text="请判断这份文件的敏感级别，并核验证据"))
    assert intent["source"] == "fallback"
    assert "data_classification" in intent["intents"]
    assert "evidence_verification" in intent["intents"]
    assert supervisor.calls[0]["parsed"] is False


def test_classify_falls_back_when_no_listed_intent_is_returned(sandbox, request_factory):
    supervisor = make_supervisor(sandbox, [classify_json(["wizardry"], "wizardry")])
    intent = supervisor.classify(request_factory(text="随便看看"))
    assert intent["source"] == "fallback"
    assert intent["intents"] == [DEFAULT_INTENT]


def test_classify_folds_a_primary_that_is_not_in_the_list_back_in(sandbox, request_factory):
    supervisor = make_supervisor(
        sandbox, [classify_json(["policy_applicability"], "compliance_ruling")]
    )
    intent = supervisor.classify(request_factory())
    assert intent["intents"] == ["policy_applicability", "compliance_ruling"]
    assert intent["primary"] == "compliance_ruling"
    assert intent["composite"] is True


def test_classify_can_be_forced_offline_by_an_exhausted_script(sandbox, request_factory):
    supervisor = make_supervisor(sandbox, [])
    intent = supervisor.classify(request_factory(text="只要策略结论"))
    assert intent["source"] == "fallback"
    assert "script exhausted" in supervisor.calls[0]["error"]


def test_intent_taxonomy_is_closed_and_documented():
    assert set(INTENTS) == {
        "data_classification",
        "policy_applicability",
        "evidence_verification",
        "remediation_planning",
        "compliance_ruling",
    }
    assert all(INTENTS.values())


# ── duty 2: task planning ─────────────────────────────────────────────────────
def plan_json(subtasks: List[Dict[str, Any]], rationale: str = "因为") -> str:
    return json.dumps({"subtasks": subtasks, "rationale": rationale}, ensure_ascii=False)


GOOD_PLAN = plan_json(
    [
        {"id": "t1", "agent": "classification", "goal": "定级", "depends_on": []},
        {"id": "t2", "agent": "policy", "goal": "判策略", "depends_on": ["t1"]},
    ],
    rationale="策略依赖级别",
)


def test_plan_accepts_a_valid_model_plan(sandbox, request_factory):
    supervisor = make_supervisor(sandbox, [GOOD_PLAN])
    plan = supervisor.plan(
        request_factory(), {"intents": ["policy_applicability"], "primary": "policy_applicability"}
    )
    assert plan["source"] == "llm"
    assert [task["agent"] for task in plan["subtasks"]] == ["classification", "policy"]
    assert plan["validation"] == {"problems": [], "repaired": False}


def test_plan_asks_once_more_after_a_validation_error(sandbox, request_factory):
    bad = plan_json([{"id": "t1", "agent": "wizard", "goal": "?", "depends_on": []}])
    supervisor = make_supervisor(sandbox, [bad, GOOD_PLAN])
    plan = supervisor.plan(request_factory(), {"intents": ["policy_applicability"]})
    assert plan["source"] == "llm_repaired"
    assert plan["validation"]["repaired"] is True
    assert any("unknown agent" in problem for problem in plan["validation"]["problems"])
    # The repair prompt must contain the complaint, not just the original request.
    assert "上一次计划不合法" in supervisor.model.prompts[1][-1]["content"]


def test_plan_falls_back_to_the_template_when_the_model_stays_wrong(sandbox, request_factory):
    bad = plan_json([{"id": "t1", "agent": "wizard", "goal": "?", "depends_on": []}])
    supervisor = make_supervisor(sandbox, [bad, bad])
    plan = supervisor.plan(request_factory(), {"intents": ["compliance_ruling"]})
    assert plan["source"] == "fallback"
    assert [task["agent"] for task in plan["subtasks"]] == [
        "classification",
        "evidence",
        "policy",
        "remediation",
    ]
    assert plan["validation"]["problems"]


def test_plan_normalises_a_string_dependency_and_missing_goals(sandbox, request_factory):
    reply = plan_json(
        [
            {"id": "t1", "agent": "classification", "depends_on": []},
            {"id": "t2", "agent": "policy", "depends_on": "t1"},
            {"agent": "evidence"},
        ]
    )
    supervisor = make_supervisor(sandbox, [reply])
    plan = supervisor.plan(request_factory(), {"intents": ["compliance_ruling"]})
    assert plan["source"] == "llm"
    assert plan["subtasks"][1]["depends_on"] == ["t1"]
    assert plan["subtasks"][2]["id"] == "t3"
    assert plan["subtasks"][2]["goal"] == ""


def test_template_plan_covers_every_intent_shape():
    assert [t["agent"] for t in template_plan(["data_classification"])] == ["classification"]
    assert [t["agent"] for t in template_plan(["evidence_verification"])] == ["evidence"]
    assert [t["agent"] for t in template_plan(["policy_applicability"])] == [
        "classification",
        "policy",
    ]
    mixed = template_plan(["data_classification", "policy_applicability", "evidence_verification"])
    assert [t["agent"] for t in mixed] == ["classification", "evidence", "policy"]
    assert [t["agent"] for t in template_plan(["remediation_planning"])] == [
        "classification",
        "policy",
        "remediation",
    ]
    assert [t["agent"] for t in template_plan([])] == [
        "classification",
        "evidence",
        "policy",
        "remediation",
    ]
    full = template_plan(["compliance_ruling"])
    assert [t["agent"] for t in full] == ["classification", "evidence", "policy", "remediation"]
    assert next(t for t in full if t["agent"] == "remediation")["depends_on"] == ["t1", "t3"]


# ── duty 3: routing ───────────────────────────────────────────────────────────
def test_route_can_satisfy_requires_without_a_declared_dependency(sandbox, request_factory):
    """The plan's ordering is a hint; a specialist's `requires` is the contract."""
    supervisor = make_supervisor(sandbox, [])
    from supervisor_graph_mvp import state as st

    run_state = st.new_state(request_factory())
    run_state[st.KEY_PLAN] = {
        "subtasks": [
            {"id": "t1", "agent": "classification", "goal": "定级", "depends_on": []},
            {"id": "t2", "agent": "policy", "goal": "判策略", "depends_on": []},
        ]
    }
    update = supervisor.route(run_state)
    assert update["results"]["t2"]["output"]["status"] == "ok"
    assert update["results"]["t2"]["output"]["level_used"] == "P3"


def test_route_retries_incomplete_work_on_the_next_dispatch_call(sandbox, request_factory):
    """One execution per sub-task per call; a later call retries what is now unblocked."""
    from supervisor_graph_mvp import state as st

    supervisor = make_supervisor(sandbox, [])
    run_state = st.new_state(request_factory())
    run_state[st.KEY_PLAN] = {
        "subtasks": [
            {"id": "t1", "agent": "policy", "goal": "判策略", "depends_on": []},
            {"id": "t2", "agent": "remediation", "goal": "处置", "depends_on": []},
            {"id": "t3", "agent": "classification", "goal": "定级", "depends_on": []},
        ]
    }
    first = supervisor.route(run_state)
    # Policy and remediation ran before their input existed and said exactly what they lacked.
    assert first["results"]["t1"]["output"]["status"] == "incomplete"
    assert first["results"]["t1"]["output"]["missing_inputs"] == ["classification"]
    assert first["results"]["t2"]["output"]["status"] == "incomplete"
    # Every id executed at most once, so a re-dispatch is what repairs them.
    assert {record["attempt"] for record in first["results"].values()} == {1}
    second = supervisor.route({**run_state, "results": first["results"]})
    assert second["results"]["t1"]["output"]["status"] == "ok"
    assert second["results"]["t1"]["attempt"] == 2
    assert second["results"]["t2"]["output"]["status"] == "ok"


def test_route_synthesises_a_subtask_for_a_repair_target_the_plan_never_scheduled(
    sandbox, request_factory
):
    from supervisor_graph_mvp import state as st

    supervisor = make_supervisor(sandbox, [])
    run_state = st.new_state(request_factory())
    run_state[st.KEY_PLAN] = {
        "subtasks": [{"id": "t1", "agent": "evidence", "goal": "核验", "depends_on": []}]
    }
    update = supervisor.route(
        run_state,
        repairs=[
            {"agent": "classification", "goal": "补定级", "hints": ["C5-citation: 证据未核验"]}
        ],
        round_index=1,
    )
    assert update["repair_log"][0]["synthesized"] is True
    assert update["repair_log"][0]["subtask"] == "r1-classification"
    assert update["results"]["r1-classification"]["agent"] == "classification"
    assert update["results"]["r1-classification"]["hint"].startswith("C5-citation")


def test_route_records_an_unknown_agent_as_a_failed_subtask(sandbox, request_factory):
    from supervisor_graph_mvp import state as st

    supervisor = make_supervisor(sandbox, [])
    run_state = st.new_state(request_factory())
    run_state[st.KEY_PLAN] = {
        "subtasks": [{"id": "t1", "agent": "wizard", "goal": "?", "depends_on": []}]
    }
    update = supervisor.route(run_state)
    assert update["results"]["t1"]["ok"] is False
    assert "unknown agent" in update["results"]["t1"]["error"]


def test_route_merges_the_evidence_index_into_the_state(sandbox, request_factory):
    from supervisor_graph_mvp import state as st

    supervisor = make_supervisor(sandbox, [])
    run_state = st.new_state(request_factory(evidence=[{"id": "rule:R-01", "kind": "rule"}]))
    run_state[st.KEY_PLAN] = {
        "subtasks": [{"id": "t1", "agent": "evidence", "goal": "核验", "depends_on": []}]
    }
    update = supervisor.route(run_state)
    assert update["artifacts"] == {"rule:R-01": {"kind": "rule", "ref": "R-01", "ok": True}}


# ── duty 4: aggregation ───────────────────────────────────────────────────────
NARRATIVE = "级别 P3，按 R-03 需数据安全组审批 [rule:R-03]。"


def _aggregate_state(sandbox: Path, request_factory, evidence=None) -> Dict[str, Any]:
    from supervisor_graph_mvp import state as st

    supervisor = make_supervisor(sandbox, [])
    run_state = st.new_state(
        request_factory(
            evidence=evidence if evidence is not None else [{"id": "rule:R-03", "kind": "rule"}]
        )
    )
    run_state[st.KEY_PLAN] = {
        "subtasks": [
            {"id": "t1", "agent": "classification", "goal": "定级", "depends_on": []},
            {"id": "t2", "agent": "evidence", "goal": "核验", "depends_on": []},
            {"id": "t3", "agent": "policy", "goal": "判策略", "depends_on": ["t1"]},
            {"id": "t4", "agent": "remediation", "goal": "处置", "depends_on": ["t1", "t3"]},
        ]
    }
    update = supervisor.route(run_state)
    run_state.update(update)
    run_state["consistency"] = {"passed": True, "blocking": 0, "warnings": 0, "findings": []}
    return run_state


def test_aggregate_merges_specialist_outputs_into_one_ruling(sandbox, request_factory):
    run_state = _aggregate_state(sandbox, request_factory)
    supervisor = make_supervisor(sandbox, [NARRATIVE])
    update = supervisor.aggregate(run_state)
    ruling = update["ruling"]
    assert update["verdict"] == "review"
    assert ruling["level"] == "P3"
    assert ruling["decision"] == "review"
    assert ruling["agents"] == ["classification", "evidence", "policy", "remediation"]
    assert ruling["narrative_source"] == "llm"
    assert update["llm_calls"][0]["purpose"] == "narrative"


def test_aggregate_escalates_when_consistency_is_still_blocking(sandbox, request_factory):
    run_state = _aggregate_state(sandbox, request_factory)
    supervisor = make_supervisor(sandbox, [])
    update = supervisor.aggregate(
        run_state,
        {"passed": False, "blocking": 2, "warnings": 1, "findings": [{"severity": "blocking"}]},
    )
    assert update["verdict"] == "escalate"


def test_aggregate_can_skip_the_narrative_for_an_escalation(sandbox, request_factory):
    run_state = _aggregate_state(sandbox, request_factory)
    supervisor = make_supervisor(sandbox, [])
    update = supervisor.aggregate(run_state, narrate=False)
    assert update["ruling"]["narrative_source"] == "deterministic_template"
    assert update["llm_calls"] == []


def test_aggregate_drops_a_narrative_that_cites_something_that_does_not_exist(
    sandbox, request_factory
):
    run_state = _aggregate_state(sandbox, request_factory)
    supervisor = make_supervisor(
        sandbox, ["级别是 P4 [rule:R-99]，并且 [artifact:missing.csv] 已核验。"]
    )
    update = supervisor.aggregate(run_state)
    ruling = update["ruling"]
    assert ruling["narrative_source"] == "deterministic_template"
    assert "rule:R-99" in update["narrative"]["unknown_citations"]
    assert "artifact:missing.csv" in update["narrative"]["unknown_citations"]
    assert "R-99" not in ruling["narrative"]


def test_aggregate_falls_back_when_the_narrative_call_fails(sandbox, request_factory):
    run_state = _aggregate_state(sandbox, request_factory)
    supervisor = make_supervisor(sandbox, [])
    update = supervisor.aggregate(run_state)
    assert update["ruling"]["narrative_source"] == "deterministic_template"
    assert "script exhausted" in update["narrative"]["llm"]["error"]


def test_citation_parsing_and_grounding():
    text = "见 [rule:R-03] 与 [artifact:a.csv]，重复 [rule:R-03] 只算一次。"
    assert citations_in(text) == ["rule:R-03", "artifact:a.csv"]
    assert unknown_citations(text, ["rule:R-03"]) == ["artifact:a.csv"]
    assert unknown_citations(text, ["rule:R-03", "artifact:a.csv"]) == []


def test_deterministic_narrative_uses_only_checked_fields():
    text = deterministic_narrative(
        {
            "level": "P4",
            "verdict": "block",
            "matched_rules": [{"id": "R-04"}],
            "actions": [{"name": "阻断该外发通道", "owner": "终端安全"}],
            "evidence": {"verified": [{"id": "rule:R-04"}], "unverified": [{"id": "artifact:x"}]},
        }
    )
    assert "P4" in text and "block" in text and "R-04" in text
    assert "阻断该外发通道" in text
    assert "artifact:x" in text
