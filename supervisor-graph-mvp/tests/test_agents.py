"""Specialist-agent tests — no model, no network, no clock.

The specialists are the deterministic half of the system, so these tests are plain
input/output assertions: a known asset yields a known level, a rule table yields a known
decision, a digest mismatch is reported rather than swallowed. Any non-determinism that
creeps in here would invalidate the MVP's central claim, so it is tested at the unit
level rather than only through the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor_graph_mvp.agents.base import AgentContext, mask
from supervisor_graph_mvp.agents.classification import ClassificationAgent
from supervisor_graph_mvp.agents.evidence import EvidenceAgent, sha256_of
from supervisor_graph_mvp.agents.policy import PolicyAgent
from supervisor_graph_mvp.agents.registry import default_registry
from supervisor_graph_mvp.agents.remediation import RemediationAgent


def ctx(sandbox: Path, request, *, deps=None, hint="", attempt=1) -> AgentContext:
    return AgentContext(
        request=request,
        goal="unit",
        data_dir=sandbox,
        deps=deps or {},
        hint=hint,
        attempt=attempt,
    )


def classification_output(level: str, asset: str = "finance-export-2026-0919.csv", detectors=None):
    return {
        "status": "ok",
        "asset": asset,
        "level": level,
        "detector_names": [hit["name"] for hit in detectors or []],
        "detectors": detectors or [],
        "citations": [{"id": "artifact:{}".format(asset), "kind": "artifact"}],
    }


# ── masking helper ────────────────────────────────────────────────────────────
def test_mask_keeps_prefix_and_suffix_only():
    assert mask("13800138001", 3, 2) == "138******01"
    assert mask("abc", 3, 0) == "***"
    assert mask("abcd", 0, 0) == "****"


# ── classification ────────────────────────────────────────────────────────────
def test_classification_rolls_detector_hits_into_the_highest_level(sandbox, request_factory):
    request = request_factory(asset="finance-export-2026-0919.csv")
    output = ClassificationAgent().invoke(ctx(sandbox, request))
    assert output["status"] == "ok"
    assert output["level"] == "P3"
    names = {hit["name"] for hit in output["detectors"]}
    assert {"mobile_phone", "employee_id", "internal_domain"} <= names
    assert output["citations"] == [
        {"id": "artifact:finance-export-2026-0919.csv", "kind": "artifact"}
    ]


def test_classification_masks_every_sample(sandbox, request_factory):
    request = request_factory(asset="finance-export-2026-0919.csv")
    output = ClassificationAgent().invoke(ctx(sandbox, request))
    phone = next(hit for hit in output["detectors"] if hit["name"] == "mobile_phone")
    assert phone["samples"][0].startswith("138") and "*" in phone["samples"][0]
    assert "13800138001" not in str(output)


def test_classification_reaches_p4_for_identity_documents(sandbox, request_factory):
    request = request_factory(asset="hr-idcard-2026-0919.csv")
    output = ClassificationAgent().invoke(ctx(sandbox, request))
    assert output["level"] == "P4"
    assert {hit["name"] for hit in output["detectors"]} >= {"id_card", "bank_card"}


def test_classification_defaults_to_p1_for_a_public_asset(sandbox, request_factory):
    request = request_factory(asset="marketing-deck-2026-0919.txt")
    output = ClassificationAgent().invoke(ctx(sandbox, request))
    assert output["level"] == "P1"
    assert output["detectors"] == []


def test_classification_reports_a_missing_asset_instead_of_raising(sandbox, request_factory):
    request = request_factory(asset="does-not-exist.csv")
    output = ClassificationAgent().invoke(ctx(sandbox, request))
    assert output["status"] == "incomplete"
    assert output["missing_inputs"] == ["asset"]


# ── policy ────────────────────────────────────────────────────────────────────
def test_policy_matches_rules_from_level_channel_and_destination(sandbox, request_factory):
    request = request_factory(channel="webmail", destination_type="external")
    deps = {"classification": classification_output("P3")}
    output = PolicyAgent().invoke(ctx(sandbox, request, deps=deps))
    assert output["status"] == "ok"
    assert output["decision"] == "review"
    assert {rule["id"] for rule in output["matched_rules"]} >= {"R-02", "R-03"}
    assert output["threshold_level"] == "P2"
    assert output["level_used"] == "P3"


def test_policy_blocks_core_data_going_external(sandbox, request_factory):
    request = request_factory(asset="hr-idcard-2026-0919.csv")
    deps = {"classification": classification_output("P4", asset="hr-idcard-2026-0919.csv")}
    output = PolicyAgent().invoke(ctx(sandbox, request, deps=deps))
    assert output["decision"] == "block"
    assert "R-04" in {rule["id"] for rule in output["matched_rules"]}


def test_policy_allows_public_data(sandbox, request_factory):
    request = request_factory(asset="marketing-deck-2026-0919.txt", channel="publish")
    deps = {"classification": classification_output("P1", asset="marketing-deck-2026-0919.txt")}
    output = PolicyAgent().invoke(ctx(sandbox, request, deps=deps))
    assert output["decision"] == "allow"
    assert [rule["id"] for rule in output["matched_rules"]] == ["R-01"]


def test_policy_without_a_level_reports_incompleteness_but_still_decides(sandbox, request_factory):
    request = request_factory()
    output = PolicyAgent().invoke(ctx(sandbox, request, deps={}))
    assert output["status"] == "incomplete"
    assert output["missing_inputs"] == ["classification"]
    assert output["level_used"] == ""
    assert output["decision"] == "review"
    assert [rule["id"] for rule in output["matched_rules"]] == ["R-06"]


def test_policy_records_a_source_code_hit_as_a_rule_match(sandbox, request_factory):
    request = request_factory(asset="finance-export-2026-0919.csv", channel="publish")
    deps = {
        "classification": classification_output(
            "P3",
            detectors=[{"name": "source_code", "level": "P3", "hits": 2, "samples": ["def hand"]}],
        )
    }
    output = PolicyAgent().invoke(ctx(sandbox, request, deps=deps))
    assert "R-05" in {rule["id"] for rule in output["matched_rules"]}


# ── evidence ──────────────────────────────────────────────────────────────────
def test_evidence_verifies_rule_and_artifact_references(sandbox, request_factory):
    digest = sha256_of(sandbox / "assets" / "finance-export-2026-0919.csv")
    request = request_factory(
        evidence=[
            {"id": "rule:R-03", "kind": "rule"},
            {"id": "artifact:finance-export-2026-0919.csv", "kind": "artifact", "sha256": digest},
        ]
    )
    output = EvidenceAgent().invoke(ctx(sandbox, request))
    assert len(output["verified"]) == 2
    assert output["unverified"] == []
    assert output["index"]["artifact:finance-export-2026-0919.csv"]["sha256"] == digest


def test_evidence_flags_a_missing_artifact(sandbox, request_factory):
    request = request_factory(evidence=[{"id": "artifact:approval-ticket.txt", "kind": "artifact"}])
    output = EvidenceAgent().invoke(ctx(sandbox, request))
    assert output["verified"] == []
    assert output["unverified"][0]["reason"] == "产物不存在"


def test_evidence_flags_a_digest_mismatch(sandbox, request_factory):
    request = request_factory(
        evidence=[
            {"id": "artifact:finance-export-2026-0919.csv", "kind": "artifact", "sha256": "0" * 64}
        ]
    )
    output = EvidenceAgent().invoke(ctx(sandbox, request))
    assert "摘要不匹配" in output["unverified"][0]["reason"]


def test_evidence_flags_an_unknown_rule(sandbox, request_factory):
    request = request_factory(evidence=[{"id": "rule:R-99", "kind": "rule"}])
    output = EvidenceAgent().invoke(ctx(sandbox, request))
    assert output["unverified"][0]["reason"] == "规则表没有这条规则"


def test_evidence_reports_items_without_an_id(sandbox, request_factory):
    request = request_factory(evidence=[{"kind": "artifact"}])
    output = EvidenceAgent().invoke(ctx(sandbox, request))
    assert output["unverified"][0]["reason"] == "evidence item has no id"


def test_sha256_of_matches_hashlib(sandbox):
    import hashlib

    path = sandbox / "assets" / "finance-export-2026-0919.csv"
    assert sha256_of(path) == hashlib.sha256(path.read_bytes()).hexdigest()


# ── remediation ───────────────────────────────────────────────────────────────
def _policy_output(decision: str, rule_id: str = "R-03"):
    return {
        "status": "ok",
        "decision": decision,
        "matched_rules": [{"id": rule_id, "decision": decision}],
        "level_used": "P3",
        "citations": [{"id": "rule:{}".format(rule_id), "kind": "rule"}],
    }


def test_remediation_selects_actions_for_review(sandbox, request_factory):
    request = request_factory()
    deps = {"classification": classification_output("P3"), "policy": _policy_output("review")}
    output = RemediationAgent().invoke(ctx(sandbox, request, deps=deps))
    kinds = {action["kind"] for action in output["actions"]}
    assert {"audit", "approval", "watermark", "minimize"} <= kinds
    assert output["citations"][0]["id"].startswith("rule:")


def test_remediation_selects_blocking_actions_for_block(sandbox, request_factory):
    request = request_factory()
    deps = {
        "classification": classification_output("P4"),
        "policy": _policy_output("block", "R-04"),
    }
    output = RemediationAgent().invoke(ctx(sandbox, request, deps=deps))
    kinds = {action["kind"] for action in output["actions"]}
    assert {"block", "notify", "audit"} <= kinds
    assert "approval" not in kinds


def test_remediation_keeps_public_data_to_audit_only(sandbox, request_factory):
    request = request_factory()
    deps = {
        "classification": classification_output("P1"),
        "policy": _policy_output("allow", "R-01"),
    }
    output = RemediationAgent().invoke(ctx(sandbox, request, deps=deps))
    assert [action["kind"] for action in output["actions"]] == ["audit"]


def test_remediation_reports_missing_dependencies(sandbox, request_factory):
    request = request_factory()
    output = RemediationAgent().invoke(
        ctx(sandbox, request, deps={"policy": _policy_output("review")})
    )
    assert output["status"] == "incomplete"
    assert output["missing_inputs"] == ["classification"]


# ── contract envelope ─────────────────────────────────────────────────────────
def test_registry_exposes_four_named_specialists(sandbox):
    registry = default_registry(sandbox)
    assert registry.names == ["classification", "policy", "evidence", "remediation"]
    described = {row["name"]: row for row in registry.describe()}
    assert described["policy"]["requires"] == ["classification"]
    assert "decision" in described["policy"]["output_keys"]


def test_registry_rejects_duplicates(sandbox):
    registry = default_registry(sandbox)
    with pytest.raises(ValueError, match="duplicate agent name"):
        registry.register(ClassificationAgent())


def test_missing_required_field_marks_the_output_incomplete(sandbox, request_factory):
    class Broken(ClassificationAgent):
        name = "broken"
        required_output_keys = ("level", "detectors", "confidence")

        def run(self, context):
            return {"level": "P1", "detectors": [], "citations": []}

    output = Broken().invoke(ctx(sandbox, request_factory()))
    assert output["status"] == "incomplete"
    assert output["missing_fields"] == ["confidence"]


def test_agent_describe_reports_the_contract(sandbox):
    row = ClassificationAgent().describe()
    assert row["name"] == "classification"
    assert row["requires"] == []
    assert row["output_keys"] == ["level", "detectors"]
