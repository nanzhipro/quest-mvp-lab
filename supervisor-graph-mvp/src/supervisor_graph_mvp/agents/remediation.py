"""Remediation specialist — turns (level, decision) into owned, deadline-bound actions.

The action table (``remediation_actions.json``) is the domain's "what do we do about
it" knowledge: audit always, approve on review, block on block, watermark and
minimise from P3 up. Selecting the actions is mechanical, which is exactly why it
belongs in a deterministic agent rather than in the aggregating model. This agent is
also the most dependency-hungry one — it needs both the level and the decision — so
it is where a sloppy plan shows up first.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..loader import data_file, load_json
from ..predicates import LEVEL_ORDER, evaluate_ops, strictest_decision
from .base import AgentContext, Specialist


class RemediationAgent(Specialist):
    """Chooses the disposal actions for the run's level and policy decision."""

    name = "remediation"
    mission = "处置动作生成（按级别与决策选表内动作）"
    requires = ("classification", "policy")
    required_output_keys = ("actions", "citations")

    def run(self, ctx: AgentContext) -> Dict[str, Any]:
        classification = ctx.dep_output("classification")
        policy = ctx.dep_output("policy")
        document = load_json(data_file(ctx.data_dir, "remediation_actions.json"))
        order = tuple(LEVEL_ORDER)
        variables = {
            "level": classification.get("level"),
            "decision": policy.get("decision"),
            "detectors": {
                str(hit.get("name")): int(hit.get("hits") or 0)
                for hit in classification.get("detectors") or []
                if isinstance(hit, dict)
            },
        }
        selected = [
            action
            for action in (document.get("actions") or [])
            if evaluate_ops(action.get("when") or [], variables, order=order)
        ]
        basis = strictest_decision(
            [str(rule.get("decision")) for rule in policy.get("matched_rules") or []],
            ("block", "review", "allow"),
        )
        rule_citation = ""
        for rule in policy.get("matched_rules") or []:
            if str(rule.get("decision")) == (basis or policy.get("decision")):
                rule_citation = str(rule.get("id"))
                break
        citations: List[Dict[str, Any]] = []
        if rule_citation:
            citations.append({"id": "rule:{}".format(rule_citation), "kind": "rule"})
        asset = classification.get("asset")
        if asset:
            citations.append({"id": "artifact:{}".format(asset), "kind": "artifact"})
        return {
            "status": "ok",
            "actions": [
                {
                    "action_id": action.get("action_id"),
                    "name": action.get("name"),
                    "kind": action.get("kind"),
                    "owner": action.get("owner"),
                    "sla_hours": action.get("sla_hours"),
                }
                for action in selected
            ],
            "level_used": variables.get("level") or "",
            "decision_used": variables.get("decision") or "",
            "citations": citations,
        }


__all__ = ["RemediationAgent"]
