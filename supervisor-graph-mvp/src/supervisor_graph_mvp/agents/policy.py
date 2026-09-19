"""Policy specialist — evaluates the external-sharing ruleset against the request.

The rule table lives in ``policy_rules.json``; this agent only supplies the
variables (level from the classification agent, channel and destination from the
request) and resolves the strictest decision among the rules that matched. When the
level is unavailable — because the plan forgot to make classification a dependency —
it does **not** guess: it applies the "unknown level" rule, marks itself
``incomplete``, and leaves the repair to the consistency checker.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..loader import data_file, load_json
from ..predicates import LEVEL_ORDER, evaluate_ops, strictest_decision, tightest_threshold
from .base import AgentContext, Specialist


class PolicyAgent(Specialist):
    """Maps (level, channel, destination) onto the matching rules and their decision."""

    name = "policy"
    mission = "外发策略适用性判定（命中规则 → 最严决策）"
    requires = ("classification",)
    required_output_keys = ("decision", "matched_rules", "citations")

    def missing_inputs(self, ctx: AgentContext) -> List[str]:
        return []

    def _variables(self, ctx: AgentContext) -> Dict[str, Any]:
        classification = ctx.dep_output("classification")
        asset = ctx.request.get("asset") or {}
        detectors = {
            str(hit.get("name")): int(hit.get("hits") or 0)
            for hit in classification.get("detectors") or []
            if isinstance(hit, dict)
        }
        return {
            "level": classification.get("level"),
            "channel": asset.get("channel"),
            "destination_type": asset.get("destination_type"),
            "detectors": detectors,
        }

    def run(self, ctx: AgentContext) -> Dict[str, Any]:
        document = load_json(data_file(ctx.data_dir, "policy_rules.json"))
        rules: List[Dict[str, Any]] = list(document.get("rules") or [])
        order = tuple(document.get("level_order") or LEVEL_ORDER)
        precedence = tuple(document.get("decision_precedence") or ("block", "review", "allow"))
        variables = self._variables(ctx)
        matched = [
            rule for rule in rules if evaluate_ops(rule.get("when") or [], variables, order=order)
        ]
        decision = strictest_decision([str(rule.get("decision")) for rule in matched], precedence)
        level_known = bool(variables.get("level"))
        return {
            "status": "ok" if level_known else "incomplete",
            "missing_inputs": [] if level_known else ["classification"],
            "reason": ""
            if level_known
            else "classification level unavailable; applied the unknown-level rule",
            "decision": decision or "review",
            "matched_rules": [
                {
                    "id": rule.get("id"),
                    "name": rule.get("name"),
                    "decision": rule.get("decision"),
                    "threshold_level": rule.get("threshold_level"),
                    "owner": rule.get("owner"),
                }
                for rule in matched
            ],
            "threshold_level": tightest_threshold(
                [str(rule.get("threshold_level")) for rule in matched], order
            ),
            "level_used": variables.get("level") or "",
            "channel": variables.get("channel") or "",
            "destination_type": variables.get("destination_type") or "",
            "rules_evaluated": len(rules),
            "citations": [
                {"id": "rule:{}".format(rule.get("id")), "kind": "rule", "name": rule.get("name")}
                for rule in matched
            ],
        }


__all__ = ["PolicyAgent"]
