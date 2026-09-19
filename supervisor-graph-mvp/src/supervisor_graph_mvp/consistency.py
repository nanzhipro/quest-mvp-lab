"""The consistency checker — the reason a Supervisor is worth having at all.

Aggregating four specialists' outputs is easy; *knowing whether they agree* is the
hard part, and it is the one duty that must not be delegated to the same model that
produced the plan. So this module is a fixed, ordered list of checks over the run's
state, each one able to say three things: what is wrong, how bad it is, and **who
must redo what** (``repair``). Blocking findings become the repair work handed back
to the dispatch node; whatever survives the repair budget becomes an escalation.

The rules are deliberately brittle in one direction only: they never block on a
*style* problem, and they never pass something they could not verify. Checks that
merely degrade quality (an ungrounded narrative) are warnings.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from . import state as st
from .jsonio import Finding
from .predicates import LEVEL_ORDER, level_rank

REPAIR_AGENT = "repair_target"


class ConsistencyChecker:
    """Runs the rule set and returns ``(findings, report)`` for one snapshot of state."""

    def __init__(
        self,
        *,
        level_order: Sequence[str] = LEVEL_ORDER,
        agent_names: Sequence[str] = (),
    ) -> None:
        self.level_order = tuple(level_order)
        self.agent_names = tuple(agent_names)
        self.rules = (
            self._rule_plan_completeness,
            self._rule_agent_contract,
            self._rule_level_known,
            self._rule_decision_coherence,
            self._rule_evidence_unverified,
            self._rule_citation_grounding,
            self._rule_remediation_coherence,
            self._rule_cross_agent_agreement,
            self._rule_narrative_grounding,
        )

    # ── public API ────────────────────────────────────────────────────────────
    def check(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        findings: List[Finding] = []
        for rule in self.rules:
            findings.extend(rule(run_state))
        blocking = [finding for finding in findings if finding.blocking]
        report = {
            "passed": not blocking,
            "checked_rules": len(self.rules),
            "blocking": len(blocking),
            "warnings": len(findings) - len(blocking),
            "findings": [finding.as_dict() for finding in findings],
            "blocking_ids": [finding.id for finding in blocking],
        }
        return {"findings": [finding.as_dict() for finding in findings], "report": report}

    # ── helper views ──────────────────────────────────────────────────────────
    def _output(self, run_state: Mapping[str, Any], agent: str) -> Dict[str, Any]:
        """The agent's latest successful output, regardless of completeness."""
        return st.agent_output(run_state, agent) or {}

    def _complete(self, run_state: Mapping[str, Any], agent: str) -> Dict[str, Any]:
        """The agent's output only when it declared itself complete.

        An ``incomplete`` output is C7's business (a missing input or field). Judging it
        again under the coherence rules would blame the wrong specialist: the message
        would say "the disposal lacks a level" when the real defect is "the disposal
        never got its policy decision".
        """
        output = self._output(run_state, agent)
        return output if str(output.get("status", "ok")) == "ok" else {}

    def _repair(self, agent: str, goal: str) -> Dict[str, str]:
        return {"agent": agent, "goal": goal}

    def _finding(
        self,
        rule: str,
        message: str,
        *,
        severity: str = "blocking",
        repair: Mapping[str, str] | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> Finding:
        return Finding(
            id="{}#{}".format(rule, message[:24]),
            rule=rule,
            severity=severity,
            message=message,
            repair=dict(repair) if repair else None,
            detail=dict(detail or {}),
        )

    # ── rules ─────────────────────────────────────────────────────────────────
    def _rule_plan_completeness(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C1: every planned sub-task ran, and ran successfully."""
        findings: List[Finding] = []
        for task in st.subtasks(run_state):
            task_id = str(task.get("id"))
            agent = str(task.get("agent"))
            result = st.result_of(run_state, task_id)
            if result is None:
                findings.append(
                    self._finding(
                        "C1-completeness",
                        "计划中的子任务 {}（{}）没有执行结果".format(task_id, agent),
                        repair=self._repair(agent, str(task.get("goal") or agent)),
                        detail={"subtask": task_id, "agent": agent},
                    )
                )
                continue
            if not result.get("ok"):
                findings.append(
                    self._finding(
                        "C1-completeness",
                        "子任务 {}（{}）执行失败：{}".format(
                            task_id, agent, str(result.get("error") or "unknown")
                        ),
                        repair=self._repair(agent, str(task.get("goal") or agent)),
                        detail={"subtask": task_id, "agent": agent},
                    )
                )
        return findings

    def _rule_agent_contract(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C7: a specialist answered with something other than 'ok' (missing inputs/fields)."""
        findings: List[Finding] = []
        for task_id, result in sorted((run_state.get(st.KEY_RESULTS) or {}).items()):
            if not isinstance(result, Mapping) or not result.get("ok"):
                continue
            output = result.get("output") or {}
            if str(output.get("status", "ok")) == "ok":
                continue
            agent = str(result.get("agent") or "")
            gaps = output.get("missing_inputs") or output.get("missing_fields") or []
            target = agent
            if gaps:
                first = str(gaps[0])
                known = self.agent_names or st.agent_names(run_state)
                # If the missing input was never produced, the *producer* owes the work;
                # if it exists, the consumer simply has to run again now that it does.
                if first in known and not st.agent_output(run_state, first):
                    target = first
            findings.append(
                self._finding(
                    "C7-contract",
                    "{} 的输出不完整（缺 {}）".format(
                        agent, ", ".join(str(gap) for gap in gaps) or "必要字段"
                    ),
                    repair=self._repair(target, "补齐 {} 缺失的输入或字段".format(agent)),
                    detail={"subtask": task_id, "agent": agent, "missing": [str(g) for g in gaps]},
                )
            )
        return findings

    def _rule_level_known(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C2a: no decision may be issued while the level is unknown."""
        findings: List[Finding] = []
        classification = self._output(run_state, "classification")
        level = str(classification.get("level") or "")
        policy = self._complete(run_state, "policy")
        if policy and not str(policy.get("level_used") or ""):
            findings.append(
                self._finding(
                    "C2-level-unknown",
                    "策略判定缺少分级依据（level_used 为空），不得据此放行或裁定",
                    repair=self._repair("classification", "重新扫描资产并给出级别"),
                )
            )
        remediation = self._complete(run_state, "remediation")
        if remediation and not str(remediation.get("level_used") or ""):
            findings.append(
                self._finding(
                    "C2-level-unknown",
                    "处置建议缺少分级依据（level_used 为空）",
                    repair=self._repair("classification", "重新扫描资产并给出级别"),
                )
            )
        if not level and (policy or remediation):
            findings.append(
                self._finding(
                    "C2-level-unknown",
                    "本次运行没有产出资产级别，裁决不成立",
                    repair=self._repair("classification", "重新扫描资产并给出级别"),
                )
            )
        return findings

    def _rule_decision_coherence(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C3: the decision must respect the level it claims to be based on."""
        findings: List[Finding] = []
        policy = self._complete(run_state, "policy")
        classification = self._complete(run_state, "classification")
        if not policy:
            return findings
        decision = str(policy.get("decision") or "")
        threshold = str(policy.get("threshold_level") or "")
        level = str(classification.get("level") or policy.get("level_used") or "")
        level_index = level_rank(level, self.level_order)
        threshold_index = level_rank(threshold, self.level_order)
        if (
            decision == "allow"
            and level_index is not None
            and threshold_index is not None
            and level_index > threshold_index
        ):
            findings.append(
                self._finding(
                    "C3-decision",
                    "级别 {} 高于放行阈值 {}，却给出 allow".format(level, threshold),
                    repair=self._repair("policy", "按命中的最严规则重新判决策"),
                    detail={"level": level, "threshold": threshold},
                )
            )
        if decision == "block" and not policy.get("matched_rules"):
            findings.append(
                self._finding(
                    "C3-decision",
                    "给出了 block 决策但没有任何命中规则作为依据",
                    repair=self._repair("policy", "重新给出 block 的规则依据或调整决策"),
                )
            )
        if not decision:
            findings.append(
                self._finding(
                    "C3-decision",
                    "策略判定没有给出 decision",
                    repair=self._repair("policy", "重新判决策"),
                )
            )
        return findings

    def _rule_evidence_unverified(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C4: a claim whose evidence cannot be re-verified blocks the ruling."""
        findings: List[Finding] = []
        evidence = self._output(run_state, "evidence")
        unverified = list(evidence.get("unverified") or [])
        for item in unverified:
            findings.append(
                self._finding(
                    "C4-evidence",
                    "证据未能核验：{}（{}）".format(
                        item.get("id") or "?", item.get("reason") or "原因未知"
                    ),
                    repair=self._repair("evidence", "重新核验该证据或确认其不存在"),
                    detail={"evidence": dict(item)},
                )
            )
        if (
            evidence
            and not (evidence.get("verified") or [])
            and (run_state.get(st.KEY_REQUEST) or {}).get("evidence")
        ):
            findings.append(
                self._finding(
                    "C4-evidence",
                    "请求带了证据清单，但没有一条通过核验",
                    repair=self._repair("evidence", "重新核验证据清单"),
                )
            )
        return findings

    def _rule_citation_grounding(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C5: every citation resolves against the rule table or a verified artifact."""
        findings: List[Finding] = []
        known = st.known_citations(run_state)
        index_empty = not (run_state.get(st.KEY_ARTIFACTS) or {})
        verifier = (
            "evidence" if "evidence" in (self.agent_names or st.agent_names(run_state)) else ""
        )
        for task_id, result in sorted((run_state.get(st.KEY_RESULTS) or {}).items()):
            if not isinstance(result, Mapping) or not result.get("ok"):
                continue
            output = result.get("output") or {}
            for citation in output.get("citations") or []:
                cid = str(citation.get("id") if isinstance(citation, Mapping) else citation)
                if not cid:
                    continue
                if cid.startswith("rule:") and cid not in known:
                    # A rule citation is resolvable from the table even when the request
                    # did not list it as evidence, so absence of evidence is not a defect.
                    continue
                if cid in known:
                    continue
                citing_agent = str(result.get("agent"))
                # An artifact nobody verified means the *verifier* owes work — rerunning the
                # citing agent would only repeat the same unverifiable claim.
                if cid.startswith("artifact:") and index_empty and verifier:
                    target, goal = verifier, "核验该产物并发布证据索引"
                else:
                    target, goal = citing_agent, "只引用已核验的证据或补齐核验"
                findings.append(
                    self._finding(
                        "C5-citation",
                        "{} 引用了无法核验的证据 {}".format(citing_agent, cid),
                        repair=self._repair(target, goal),
                        detail={"subtask": task_id, "citation": cid},
                    )
                )
        return findings

    def _rule_remediation_coherence(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C6: the disposal actions must match the decision and the level."""
        findings: List[Finding] = []
        remediation = self._complete(run_state, "remediation")
        policy = self._complete(run_state, "policy")
        classification = self._complete(run_state, "classification")
        if not remediation:
            return findings
        kinds = {str(action.get("kind") or "") for action in remediation.get("actions") or []}
        decision = str(policy.get("decision") or remediation.get("decision_used") or "")
        level = str(classification.get("level") or remediation.get("level_used") or "")
        if not kinds:
            findings.append(
                self._finding(
                    "C6-remediation",
                    "处置建议为空",
                    repair=self._repair("remediation", "重新按级别与决策选择处置动作"),
                )
            )
            return findings
        if "audit" not in kinds:
            findings.append(
                self._finding(
                    "C6-remediation",
                    "处置动作缺少审计留痕（kind=audit）",
                    repair=self._repair("remediation", "补上审计留痕动作"),
                )
            )
        if decision == "block" and "block" not in kinds:
            findings.append(
                self._finding(
                    "C6-remediation",
                    "决策为 block，处置动作里却没有阻断（kind=block）",
                    repair=self._repair("remediation", "补上阻断动作"),
                )
            )
        if decision == "review" and "approval" not in kinds:
            findings.append(
                self._finding(
                    "C6-remediation",
                    "决策为 review，处置动作里却没有审批（kind=approval）",
                    repair=self._repair("remediation", "补上审批动作"),
                )
            )
        rank = level_rank(level, self.level_order)
        p3_rank = level_rank("P3", self.level_order)
        if rank is not None and p3_rank is not None and rank >= p3_rank and "minimize" not in kinds:
            findings.append(
                self._finding(
                    "C6-remediation",
                    "级别 {} 至少应包含最小化/水印类处置".format(level),
                    repair=self._repair("remediation", "补上最小化与水印动作"),
                )
            )
        return findings

    def _rule_cross_agent_agreement(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C8: specialists must not disagree about the same fact."""
        findings: List[Finding] = []
        classification = self._complete(run_state, "classification")
        level = str(classification.get("level") or "")
        for agent in ("policy", "remediation"):
            output = self._complete(run_state, agent)
            used = str(output.get("level_used") or "")
            if level and used and used != level:
                findings.append(
                    self._finding(
                        "C8-agreement",
                        "{} 使用的级别 {} 与分级结论 {} 不一致".format(agent, used, level),
                        repair=self._repair(agent, "按分级结论重建输出"),
                        detail={"agent": agent, "used": used, "expected": level},
                    )
                )
        policy_decision = str(self._complete(run_state, "policy").get("decision") or "")
        remediation_decision = str(
            self._complete(run_state, "remediation").get("decision_used") or ""
        )
        if policy_decision and remediation_decision and policy_decision != remediation_decision:
            findings.append(
                self._finding(
                    "C8-agreement",
                    "处置建议依据的决策 {} 与策略判定 {} 不一致".format(
                        remediation_decision, policy_decision
                    ),
                    repair=self._repair("remediation", "按策略判定重建处置动作"),
                    detail={"policy": policy_decision, "remediation": remediation_decision},
                )
            )
        policy_rules = {
            str(rule.get("id"))
            for rule in self._complete(run_state, "policy").get("matched_rules") or []
        }
        cited_rules = {
            str(citation.get("id")).split(":", 1)[1]
            for citation in self._complete(run_state, "remediation").get("citations") or []
            if str(citation.get("id")).startswith("rule:")
        }
        unknown_basis = cited_rules - policy_rules
        if cited_rules and unknown_basis:
            findings.append(
                self._finding(
                    "C8-agreement",
                    "处置建议引用了策略判定未命中的规则：{}".format(
                        ", ".join(sorted(unknown_basis))
                    ),
                    repair=self._repair("remediation", "改用命中规则作为依据"),
                    detail={"unknown": sorted(unknown_basis)},
                )
            )
        return findings

    def _rule_narrative_grounding(self, run_state: Mapping[str, Any]) -> List[Finding]:
        """C9: the aggregated narrative may only cite ids that exist (warning, not blocking)."""
        narrative = run_state.get(st.KEY_NARRATIVE) or {}
        text = str(narrative.get("text") or "")
        if not text:
            return []
        unknown = [cid for cid in narrative.get("unknown_citations") or []]
        if not unknown:
            return []
        return [
            self._finding(
                "C9-narrative",
                "聚合叙述引用了不存在的证据 {}，已回退到确定性模板".format(", ".join(unknown)),
                severity="warning",
                detail={"unknown": list(unknown)},
            )
        ]
