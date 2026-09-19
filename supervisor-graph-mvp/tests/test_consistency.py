"""Consistency-checker tests: every rule, passing and failing.

These are the tests that matter most for the MVP's claim. If a rule can silently pass
something it should have blocked, the orchestration layer is decoration — so each rule
gets a positive case (nothing reported) and a negative case (exactly the right finding,
with the right repair target).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from supervisor_graph_mvp import state as st
from supervisor_graph_mvp.consistency import ConsistencyChecker

AGENTS = ("classification", "evidence", "policy", "remediation")


def result(
    agent: str, output: Dict[str, Any], *, ok: bool = True, subtask: str = "t"
) -> Dict[str, Any]:
    payload = {"status": "ok", "citations": [], "missing_inputs": [], **output}
    return {
        "subtask": subtask,
        "agent": agent,
        "goal": "unit",
        "ok": ok,
        "error": "" if ok else "boom",
        "output": payload,
        "attempt": 1,
        "hint": "",
        "duration_ms": 0,
    }


PLAN = {
    "subtasks": [
        {"id": "t1", "agent": "classification", "goal": "定级", "depends_on": []},
        {"id": "t2", "agent": "evidence", "goal": "核验", "depends_on": []},
        {"id": "t3", "agent": "policy", "goal": "判策略", "depends_on": ["t1"]},
        {"id": "t4", "agent": "remediation", "goal": "处置", "depends_on": ["t1", "t3"]},
    ]
}


def clean_results() -> Dict[str, Any]:
    return {
        "t1": result(
            "classification",
            {
                "level": "P3",
                "detectors": [
                    {"name": "mobile_phone", "level": "P3", "hits": 1, "samples": ["138******01"]}
                ],
                "asset": "finance-export-2026-0919.csv",
                "citations": [{"id": "artifact:finance-export-2026-0919.csv", "kind": "artifact"}],
            },
            subtask="t1",
        ),
        "t2": result(
            "evidence",
            {
                "verified": [{"id": "artifact:finance-export-2026-0919.csv"}],
                "unverified": [],
                "index": {
                    "artifact:finance-export-2026-0919.csv": {"kind": "artifact", "ok": True}
                },
                "citations": [{"id": "artifact:finance-export-2026-0919.csv", "kind": "artifact"}],
            },
            subtask="t2",
        ),
        "t3": result(
            "policy",
            {
                "decision": "review",
                "matched_rules": [{"id": "R-03", "decision": "review", "threshold_level": "P3"}],
                "threshold_level": "P3",
                "level_used": "P3",
                "citations": [{"id": "rule:R-03", "kind": "rule"}],
            },
            subtask="t3",
        ),
        "t4": result(
            "remediation",
            {
                "actions": [
                    {"action_id": "A-01", "kind": "audit"},
                    {"action_id": "A-02", "kind": "approval"},
                    {"action_id": "A-06", "kind": "minimize"},
                ],
                "level_used": "P3",
                "decision_used": "review",
                "citations": [{"id": "rule:R-03", "kind": "rule"}],
            },
            subtask="t4",
        ),
    }


def build_state(results: Optional[Dict[str, Any]] = None, **extra: Any) -> Dict[str, Any]:
    run_state = st.new_state({"id": "unit", "evidence": []})
    run_state[st.KEY_PLAN] = dict(PLAN)
    run_state[st.KEY_RESULTS] = clean_results() if results is None else results
    run_state[st.KEY_ARTIFACTS] = {
        "artifact:finance-export-2026-0919.csv": {"kind": "artifact", "ok": True}
    }
    run_state.update(extra)
    return run_state


@pytest.fixture
def checker() -> ConsistencyChecker:
    return ConsistencyChecker(agent_names=AGENTS)


def rules_of(findings: List[Dict[str, Any]]) -> List[str]:
    return [finding["rule"] for finding in findings]


def test_a_clean_run_passes_every_rule(checker):
    outcome = checker.check(build_state())
    assert outcome["report"]["passed"] is True
    assert outcome["findings"] == []
    assert outcome["report"]["checked_rules"] == 9


# ── C1 completeness ───────────────────────────────────────────────────────────
def test_missing_subtask_result_is_blocking(checker):
    results = clean_results()
    del results["t4"]
    findings = checker.check(build_state(results))["findings"]
    assert "C1-completeness" in rules_of(findings)
    finding = next(item for item in findings if item["rule"] == "C1-completeness")
    assert finding["repair"]["agent"] == "remediation"


def test_failed_subtask_is_blocking(checker):
    results = clean_results()
    results["t3"] = result("policy", {}, ok=False, subtask="t3")
    findings = checker.check(build_state(results))["findings"]
    assert "C1-completeness" in rules_of(findings)
    assert any("执行失败" in item["message"] for item in findings)


# ── C7 contract ───────────────────────────────────────────────────────────────
def test_incomplete_output_is_blocking(checker):
    results = clean_results()
    results["t4"]["output"] = {
        "status": "incomplete",
        "missing_inputs": ["policy"],
        "citations": [],
    }
    findings = checker.check(build_state(results))["findings"]
    assert "C7-contract" in rules_of(findings)
    finding = next(item for item in findings if item["rule"] == "C7-contract")
    # policy exists, so the consumer owes the retry, not the producer.
    assert finding["repair"]["agent"] == "remediation"


def test_missing_producer_is_the_one_asked_to_retry(checker):
    results = clean_results()
    del results["t3"]
    results["t4"]["output"] = {
        "status": "incomplete",
        "missing_inputs": ["policy"],
        "citations": [],
    }
    findings = checker.check(build_state(results))["findings"]
    finding = next(item for item in findings if item["rule"] == "C7-contract")
    assert finding["repair"]["agent"] == "policy"


# ── C2 level known ────────────────────────────────────────────────────────────
def test_policy_without_a_level_blocks(checker):
    results = clean_results()
    results["t3"]["output"]["level_used"] = ""
    findings = checker.check(build_state(results))["findings"]
    assert "C2-level-unknown" in rules_of(findings)
    finding = next(item for item in findings if item["rule"] == "C2-level-unknown")
    assert finding["repair"]["agent"] == "classification"


def test_a_run_without_any_level_cannot_be_settled(checker):
    results = clean_results()
    results["t1"]["output"] = {"status": "incomplete", "citations": [], "missing_inputs": ["asset"]}
    findings = checker.check(build_state(results))["findings"]
    assert "C2-level-unknown" in rules_of(findings)


# ── C3 decision coherence ─────────────────────────────────────────────────────
def test_allowing_above_the_threshold_blocks(checker):
    results = clean_results()
    results["t3"]["output"].update(
        {"decision": "allow", "threshold_level": "P2", "level_used": "P3"}
    )
    findings = checker.check(build_state(results))["findings"]
    assert "C3-decision" in rules_of(findings)
    assert any("高于放行阈值" in item["message"] for item in findings)


def test_blocking_without_a_rule_basis_blocks(checker):
    results = clean_results()
    results["t3"]["output"].update({"decision": "block", "matched_rules": []})
    findings = checker.check(build_state(results))["findings"]
    assert any("没有任何命中规则" in item["message"] for item in findings)


def test_a_missing_decision_blocks(checker):
    results = clean_results()
    results["t3"]["output"]["decision"] = ""
    findings = checker.check(build_state(results))["findings"]
    assert any("没有给出 decision" in item["message"] for item in findings)


# ── C4 evidence ───────────────────────────────────────────────────────────────
def test_unverified_evidence_blocks_with_the_verifier_as_owner(checker):
    results = clean_results()
    results["t2"]["output"]["unverified"] = [
        {"id": "artifact:approval-ticket.txt", "reason": "产物不存在"}
    ]
    findings = checker.check(build_state(results))["findings"]
    assert "C4-evidence" in rules_of(findings)
    finding = next(item for item in findings if item["rule"] == "C4-evidence")
    assert finding["repair"]["agent"] == "evidence"


def test_a_request_with_evidence_but_nothing_verified_blocks(checker):
    results = clean_results()
    results["t2"]["output"].update({"verified": [], "index": {}})
    run_state = build_state(results)
    run_state[st.KEY_REQUEST]["evidence"] = [{"id": "artifact:x"}]
    findings = checker.check(run_state)["findings"]
    assert any("没有一条通过核验" in item["message"] for item in findings)


# ── C5 citation grounding ─────────────────────────────────────────────────────
def test_an_unverifiable_artifact_citation_blocks(checker):
    results = clean_results()
    results["t1"]["output"]["citations"] = [{"id": "artifact:unknown.csv", "kind": "artifact"}]
    findings = checker.check(build_state(results))["findings"]
    assert "C5-citation" in rules_of(findings)
    assert (
        next(item for item in findings if item["rule"] == "C5-citation")["repair"]["agent"]
        == "classification"
    )


def test_an_unverified_artifact_and_no_index_blames_the_verifier(checker):
    results = clean_results()
    run_state = build_state(results)
    run_state[st.KEY_ARTIFACTS] = {}
    findings = checker.check(run_state)["findings"]
    finding = next(item for item in findings if item["rule"] == "C5-citation")
    assert finding["repair"]["agent"] == "evidence"


def test_rule_citations_need_not_appear_in_the_evidence_list(checker):
    findings = checker.check(build_state())["findings"]
    assert findings == []


# ── C6 remediation coherence ──────────────────────────────────────────────────
def test_empty_actions_block(checker):
    results = clean_results()
    results["t4"]["output"]["actions"] = []
    findings = checker.check(build_state(results))["findings"]
    assert any("处置建议为空" in item["message"] for item in findings)


def test_missing_audit_action_blocks(checker):
    results = clean_results()
    results["t4"]["output"]["actions"] = [{"kind": "approval"}, {"kind": "minimize"}]
    findings = checker.check(build_state(results))["findings"]
    assert any("缺少审计留痕" in item["message"] for item in findings)


def test_block_decision_requires_a_block_action(checker):
    results = clean_results()
    results["t3"]["output"].update(
        {"decision": "block", "matched_rules": [{"id": "R-04", "decision": "block"}]}
    )
    results["t4"]["output"].update(
        {
            "decision_used": "block",
            "actions": [{"kind": "audit"}, {"kind": "minimize"}, {"kind": "notify"}],
        }
    )
    findings = checker.check(build_state(results))["findings"]
    assert any("却没有阻断" in item["message"] for item in findings)


def test_review_decision_requires_an_approval_action(checker):
    results = clean_results()
    results["t4"]["output"]["actions"] = [{"kind": "audit"}, {"kind": "minimize"}]
    findings = checker.check(build_state(results))["findings"]
    assert any("没有审批" in item["message"] for item in findings)


def test_sensitive_levels_require_minimisation(checker):
    results = clean_results()
    results["t4"]["output"]["actions"] = [{"kind": "audit"}, {"kind": "approval"}]
    findings = checker.check(build_state(results))["findings"]
    assert any("最小化" in item["message"] for item in findings)


# ── C8 cross-agent agreement ──────────────────────────────────────────────────
def test_a_stale_level_elsewhere_blocks(checker):
    results = clean_results()
    results["t4"]["output"]["level_used"] = "P2"
    findings = checker.check(build_state(results))["findings"]
    assert "C8-agreement" in rules_of(findings)
    assert (
        next(item for item in findings if item["rule"] == "C8-agreement")["repair"]["agent"]
        == "remediation"
    )


def test_disagreeing_decisions_block(checker):
    results = clean_results()
    results["t4"]["output"]["decision_used"] = "block"
    findings = checker.check(build_state(results))["findings"]
    assert any("依据的决策 block 与策略判定 review 不一致" in item["message"] for item in findings)


def test_citing_a_rule_the_policy_agent_did_not_match_blocks(checker):
    results = clean_results()
    results["t4"]["output"]["citations"] = [{"id": "rule:R-04", "kind": "rule"}]
    findings = checker.check(build_state(results))["findings"]
    assert any("未命中的规则" in item["message"] for item in findings)


# ── C9 narrative grounding (warning, never blocking) ──────────────────────────
def test_an_ungrounded_narrative_is_a_warning_not_a_blocker(checker):
    run_state = build_state(
        narrative={"text": "见 [rule:R-99]", "unknown_citations": ["rule:R-99"]}
    )
    outcome = checker.check(run_state)
    assert outcome["report"]["passed"] is True
    assert outcome["report"]["warnings"] == 1
    assert "C9-narrative" in rules_of(outcome["findings"])


def test_a_grounded_narrative_is_silent(checker):
    run_state = build_state(narrative={"text": "见 [rule:R-03]", "unknown_citations": []})
    assert checker.check(run_state)["findings"] == []


# ── report shape ──────────────────────────────────────────────────────────────
def test_report_counts_blocking_and_warnings_separately(checker):
    results = clean_results()
    results["t2"]["output"]["unverified"] = [{"id": "artifact:x", "reason": "产物不存在"}]
    run_state = build_state(
        results, narrative={"text": "见 [rule:R-99]", "unknown_citations": ["rule:R-99"]}
    )
    report = checker.check(run_state)["report"]
    assert report["passed"] is False
    assert report["blocking"] == 1
    assert report["warnings"] == 1
    assert report["blocking_ids"]


def test_findings_carry_a_stable_id_and_rule_name(checker):
    results = clean_results()
    del results["t4"]
    findings = checker.check(build_state(results))["findings"]
    assert all(finding["id"].startswith(finding["rule"] + "#") for finding in findings)
