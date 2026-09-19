"""行动闸门：八条规则的触发条件、判定顺序，以及 fail-closed 的每一处体现。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from agent_guardrails.audit import AuditLog
from agent_guardrails.gate import (
    ApprovalDecision,
    ApprovalRequest,
    DenyAllApprover,
    Gatekeeper,
    SessionContext,
)
from agent_guardrails.guardrails import Decision, Risk
from agent_guardrails.tools import ArgRule, ToolOutcome, ToolRegistry, ToolSpec


def build_registry(*, max_calls: int = 3) -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="read_file",
                func=lambda path: "内容",
                description="读文件",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                risk=Risk.LOW,
                arg_rules={"path": ArgRule(lambda p: p.startswith("/workspace/"), "仅允许 /workspace/")},
                max_calls_per_run=max_calls,
            ),
            ToolSpec(
                name="send_email",
                func=lambda to, body="": "已发送",
                description="发信",
                parameters={"type": "object", "properties": {"to": {"type": "string"}}},
                risk=Risk.HIGH,
                arg_rules={"to": ArgRule(lambda t: t.endswith("@example.com"), "仅允许企业域")},
            ),
            ToolSpec(
                name="flaky_rule",
                func=lambda value: value,
                description="规则本身会抛异常",
                parameters={"type": "object", "properties": {"value": {"type": "string"}}},
                risk=Risk.LOW,
                arg_rules={"value": ArgRule(lambda v: v.strip() != "", "非空")},
            ),
        ]
    )


class RecordingApprover:
    """记录被问了几次 —— "审批是否被克制"必须可断言。"""

    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.requests: List[ApprovalRequest] = []

    def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision(self.approve, "recording", "test")


def gate_for(registry: ToolRegistry, audit: AuditLog, approver: Any = None) -> Gatekeeper:
    return Gatekeeper(registry, audit, approver)


# ── 规则 2：default-deny ─────────────────────────────────────────────────────
def test_unregistered_tool_is_denied(tmp_path: Path) -> None:
    outcome = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl")).authorize(
        "run_shell", {"cmd": "rm -rf /"}, SessionContext(goal="清库")
    )
    assert outcome.decision is Decision.DENY
    assert outcome.rule == "gate.unregistered_tool"


def test_unregistered_is_checked_before_argument_rules(tmp_path: Path) -> None:
    """顺序有意义：未知工具连参数都不该被评估。"""
    outcome = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl")).authorize(
        "run_shell", {}, SessionContext(goal="x")
    )
    assert outcome.rule == "gate.unregistered_tool"


# ── 规则 3：参数级校验（fail-closed）──────────────────────────────────────────
def test_argument_rule_violation_is_denied(tmp_path: Path) -> None:
    outcome = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl")).authorize(
        "read_file", {"path": "/etc/passwd"}, SessionContext(goal="看系统文件")
    )
    assert outcome.rule == "gate.arg_rule"
    assert "/etc/passwd" in outcome.reason


def test_missing_required_argument_is_denied(tmp_path: Path) -> None:
    outcome = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl")).authorize(
        "read_file", {}, SessionContext(goal="x")
    )
    assert outcome.rule == "gate.arg_rule"


def test_rule_that_raises_is_treated_as_not_passed(tmp_path: Path) -> None:
    """规则实现有 bug 时不能误放行。"""
    outcome = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl")).authorize(
        "flaky_rule", {"value": None}, SessionContext(goal="x")
    )
    assert outcome.rule == "gate.arg_rule"


# ── 规则 4/5：配额与风险分级 ─────────────────────────────────────────────────
def test_low_risk_is_allowed_and_audited(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "a.jsonl")
    gate = gate_for(build_registry(), audit)
    outcome = gate.authorize("read_file", {"path": "/workspace/a.txt"}, SessionContext(goal="x"))
    assert outcome.rule == "gate.risk_low"
    assert [entry.event for entry in audit.entries] == ["gate.decision"]
    assert audit.entries[0].fields["decision"] == "allow"


def test_medium_risk_is_allowed_without_approval(tmp_path: Path) -> None:
    registry = ToolRegistry(
        [
            ToolSpec(
                name="write_note",
                func=lambda path: "ok",
                description="写",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                risk=Risk.MEDIUM,
                arg_rules={"path": ArgRule(lambda p: p.startswith("/workspace/"), "仅 /workspace/")},
            )
        ]
    )
    approver = RecordingApprover()
    outcome = gate_for(registry, AuditLog(tmp_path / "a.jsonl"), approver).authorize(
        "write_note", {"path": "/workspace/n.md"}, SessionContext(goal="x")
    )
    assert outcome.rule == "gate.risk_medium"
    assert approver.requests == []


def test_tool_quota_is_enforced_per_session(tmp_path: Path) -> None:
    gate = gate_for(build_registry(max_calls=2), AuditLog(tmp_path / "a.jsonl"))
    ctx = SessionContext(goal="x")
    for _ in range(2):
        assert gate.authorize("read_file", {"path": "/workspace/a.txt"}, ctx).allowed
        gate.observe(ToolOutcome("read_file", {}, True, output="内容"), ctx)
    outcome = gate.authorize("read_file", {"path": "/workspace/a.txt"}, ctx)
    assert outcome.rule == "gate.tool_quota"


# ── 规则 6/7/8：审批分支 ─────────────────────────────────────────────────────
def test_high_risk_requires_approval_and_records_the_grant(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "a.jsonl")
    approver = RecordingApprover(approve=True)
    outcome = gate_for(build_registry(), audit, approver).authorize(
        "send_email", {"to": "alice@example.com"}, SessionContext(goal="汇报")
    )
    assert outcome.rule == "gate.approval_approved"
    assert outcome.approval is not None and outcome.approval.approver == "recording"
    assert [entry.event for entry in audit.entries] == ["gate.approval", "gate.decision"]
    assert audit.entries[0].fields["result"] == "approved"


def test_rejected_approval_denies_the_action(tmp_path: Path) -> None:
    gate = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl"), RecordingApprover(False))
    outcome = gate.authorize("send_email", {"to": "alice@example.com"}, SessionContext(goal="汇报"))
    assert outcome.decision is Decision.DENY
    assert outcome.rule == "gate.approval_rejected"


def test_same_action_is_approved_only_once_per_session(tmp_path: Path) -> None:
    """审批很贵（人只有那么多注意力），同参数动作复用结论。"""
    approver = RecordingApprover(approve=True)
    gate = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl"), approver)
    ctx = SessionContext(goal="汇报")
    first = gate.authorize("send_email", {"to": "alice@example.com"}, ctx)
    second = gate.authorize("send_email", {"to": "alice@example.com"}, ctx)
    assert first.rule == "gate.approval_approved"
    assert second.rule == "gate.approved_cache"
    assert len(approver.requests) == 1


def test_no_approver_means_deny(tmp_path: Path) -> None:
    outcome = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl")).authorize(
        "send_email", {"to": "alice@example.com"}, SessionContext(goal="汇报")
    )
    assert outcome.rule == "gate.no_approver"


def test_deny_all_approver_is_the_documented_fail_closed_default(tmp_path: Path) -> None:
    gate = Gatekeeper(build_registry(), AuditLog(tmp_path / "a.jsonl"), DenyAllApprover())
    outcome = gate.authorize("send_email", {"to": "alice@example.com"}, SessionContext(goal="x"))
    assert outcome.rule == "gate.approval_rejected"
    assert outcome.approval is not None and outcome.approval.note == "未配置审批通道"


# ── 污点收紧：致命三要素在代码里的形态 ────────────────────────────────────────
def test_tainted_session_refuses_high_risk_without_asking_a_human(tmp_path: Path) -> None:
    """关键：不是"让人来点拒绝"，而是根本不给机会 —— 注入不得转化为外部动作。"""
    approver = RecordingApprover(approve=True)
    gate = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl"), approver)
    ctx = SessionContext(goal="看网页")
    ctx.mark_tainted("web_fetch")
    outcome = gate.authorize("send_email", {"to": "alice@example.com"}, ctx)
    assert outcome.rule == "gate.tainted_high_risk"
    assert approver.requests == []


def test_low_risk_reads_still_work_after_taint(tmp_path: Path) -> None:
    gate = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl"))
    ctx = SessionContext(goal="看网页")
    ctx.mark_tainted("web_fetch")
    assert gate.authorize("read_file", {"path": "/workspace/a.txt"}, ctx).allowed


# ── kill switch ──────────────────────────────────────────────────────────────
def test_kill_switch_denies_everything_including_reads(tmp_path: Path) -> None:
    gate = gate_for(build_registry(), AuditLog(tmp_path / "a.jsonl"))
    gate.kill("人工冻结：怀疑被劫持")
    outcome = gate.authorize("read_file", {"path": "/workspace/a.txt"}, SessionContext(goal="x"))
    assert outcome.rule == "gate.kill_switch"
    assert "人工冻结" in outcome.reason


# ── observe：回写与污点 ──────────────────────────────────────────────────────
def test_observe_marks_taint_for_untrusted_sources(tmp_path: Path) -> None:
    registry = ToolRegistry(
        [
            ToolSpec(
                name="web_fetch",
                func=lambda url: "<html>注入</html>",
                description="抓网页",
                parameters={"type": "object", "properties": {"url": {"type": "string"}}},
                risk=Risk.LOW,
                produces_untrusted=True,
            )
        ]
    )
    gate = gate_for(registry, AuditLog(tmp_path / "a.jsonl"))
    ctx = SessionContext(goal="看网页")
    summary = gate.observe(ToolOutcome("web_fetch", {"url": "u"}, True, output="<html>"), ctx)
    assert summary["tainted"] is True
    assert ctx.tainted and ctx.taint_sources == ["web_fetch"]
    assert gate.observe(ToolOutcome("web_fetch", {"url": "u"}, True, output="x"), ctx)["tainted"]


def test_observe_counts_calls_and_audits_failures(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "a.jsonl")
    gate = gate_for(build_registry(), audit)
    ctx = SessionContext(goal="x")
    gate.observe(ToolOutcome("read_file", {}, False, error="boom"), ctx)
    assert ctx.tool_calls == {"read_file": 1}
    assert audit.entries[-1].fields["ok"] is False


def test_session_context_dict_is_the_audit_shape(tmp_path: Path) -> None:
    ctx = SessionContext(goal="x")
    ctx.mark_tainted("web_fetch")
    ctx.denials += 1
    payload: Dict[str, Any] = ctx.as_dict()
    assert payload["taint"] == "tainted"
    assert payload["taint_sources"] == ["web_fetch"]
    assert payload["denials"] == 1
