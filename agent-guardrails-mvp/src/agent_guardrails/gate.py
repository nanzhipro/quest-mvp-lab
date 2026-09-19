"""行动闸门 —— 整个护栏体系的心脏，也是唯一不可省略的一层。

职责一句话：**模型想做什么，能不能变成现实**。所有判定都在模型之外的这段确定性代码里完成，
判定顺序严格遵循 deny 优先（最严格的匹配最先求值）：

1. 运行时 kill switch 已触发 → 拒绝
2. 工具不在 allowlist → 拒绝（default-deny）
3. 参数级规则不通过（含规则评估异常）→ 拒绝（fail-closed）
4. 单工具调用配额用尽 → 拒绝
5. 风险分级：LOW 放行 / MEDIUM 限流放行 / HIGH 进入审批分支
6. HIGH + 会话已被不可信内容污染 → **不经审批直接拒绝**（"致命三要素"防线）
7. HIGH 且无审批通道 → 拒绝（fail-closed，绝不"没人看就放行"）
8. HIGH + 审批通过 → 放行并把动作摘要记入白名单（同参数不重复打扰人）

审批必须克制：只对真正不可逆的动作触发。研究显示用户会批准约 93% 的权限弹窗，
滥触发会让闸门形同虚设。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Set

from . import obs
from .audit import AuditLog
from .guardrails import Decision, Risk, Verdict
from .tools import ToolOutcome, ToolRegistry


def _action_digest(tool: str, args: Dict[str, Any]) -> str:
    raw = "{}|{}".format(tool, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TaintState(Enum):
    """会话是否存在"不可信内容已进入上下文"的污染。"""

    CLEAN = "clean"
    TAINTED = "tainted"


@dataclass
class SessionContext:
    """一次会话的可信状态：污点、配额、已审批动作、会话标识。

    这些状态由编排器维护 —— 模型自己"会忘记、会被说服"，不能让它持有。
    """

    goal: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    taint: TaintState = TaintState.CLEAN
    taint_sources: List[str] = field(default_factory=list)
    tool_calls: Dict[str, int] = field(default_factory=dict)
    approved_actions: Set[str] = field(default_factory=set)
    denials: int = 0
    approvals_granted: int = 0
    approvals_refused: int = 0

    @property
    def tainted(self) -> bool:
        return self.taint is TaintState.TAINTED

    def mark_tainted(self, source: str) -> bool:
        """打污点（幂等）。返回是否是"本次首次"污染。"""
        first = not self.tainted
        self.taint = TaintState.TAINTED
        if source not in self.taint_sources:
            self.taint_sources.append(source)
        return first

    def as_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "taint": self.taint.value,
            "taint_sources": list(self.taint_sources),
            "tool_calls": dict(self.tool_calls),
            "denials": self.denials,
            "approvals_granted": self.approvals_granted,
            "approvals_refused": self.approvals_refused,
        }


@dataclass(frozen=True)
class ApprovalRequest:
    """递给审批人的四要素：动作、目标、来源、不可逆后果。"""

    session: str
    tool: str
    args: Dict[str, Any]
    risk: Risk
    goal: str
    why: str

    def render(self) -> str:
        return "动作={tool} 目标={target} 风险={risk} 来源目标={goal} 依据={why}".format(
            tool=self.tool,
            target=json.dumps(self.args, ensure_ascii=False, default=str),
            risk=self.risk.value,
            goal=self.goal or "-",
            why=self.why,
        )


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    approver: str = "unknown"
    note: str = ""


class Approver(Protocol):
    """审批通道：真实部署接 IM / 工单，MVP 里可以是自动策略或交互式提问。"""

    def __call__(self, request: ApprovalRequest) -> ApprovalDecision: ...


class DenyAllApprover:
    """没有审批通道时的行为 —— 一切高危动作拒绝（fail-closed）。"""

    def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(False, "deny-all", "未配置审批通道")


class ScriptedApprover:
    """规则式审批：把"人的判断"表达成可复现的谓词（离线演练与测试用）。"""

    def __init__(self, predicate, *, name: str = "scripted") -> None:  # type: ignore[no-untyped-def]
        self._predicate = predicate
        self._name = name

    def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        approved = bool(self._predicate(request))
        return ApprovalDecision(approved, self._name, "按脚本规则{}".format("批准" if approved else "拒绝"))


class ConsoleApprover:
    """交互式审批：非 TTY（CI / 管道）下一律拒绝，不给"默默放行"的机会。"""

    def __init__(self, *, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s

    def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        if not sys.stdin.isatty():
            return ApprovalDecision(False, "console", "非交互环境，无人可审批")
        prompt = "\n[人工审批] {}\n批准执行？[y/N] ".format(request.render())
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return ApprovalDecision(False, "console", "审批中断")
        return ApprovalDecision(answer in {"y", "yes"}, "console", "人工回答={}".format(answer or "n"))


@dataclass(frozen=True)
class GateOutcome:
    """闸门结论：三态判定 + 依据 + 命中的规则名，若走过审批还带上审批记录。"""

    verdict: Verdict
    approval: Optional[ApprovalDecision] = None

    @property
    def decision(self) -> Decision:
        return self.verdict.decision

    @property
    def allowed(self) -> bool:
        return self.verdict.decision is Decision.ALLOW

    @property
    def rule(self) -> str:
        return self.verdict.rule

    @property
    def reason(self) -> str:
        return self.verdict.reason


class Gatekeeper:
    """授权闸门。所有策略判断都收在这里，循环与工具层不留安全逻辑。"""

    def __init__(
        self,
        registry: ToolRegistry,
        audit: AuditLog,
        approver: Optional[Approver] = None,
        *,
        kill_switch: bool = False,
    ) -> None:
        self.registry = registry
        self.audit = audit
        self.approver = approver
        self.killed = kill_switch
        self.kill_reason = ""

    def kill(self, reason: str) -> None:
        """冻结本次运行的全部动作权限（手动或探针自动触发）。"""
        self.killed = True
        self.kill_reason = reason
        obs.event("gate.kill_switch", "运行时动作权限已冻结", level=logging.CRITICAL, reason=reason)
        self.audit.record("gate.kill_switch", reason=reason)

    # ── 授权 ─────────────────────────────────────────────────────────────────
    def authorize(self, tool: str, args: Dict[str, Any], ctx: SessionContext) -> GateOutcome:
        outcome = self._decide(tool, args, ctx)
        level = logging.INFO if outcome.allowed else logging.WARNING
        obs.event(
            "gate.decision",
            outcome.reason,
            level=level,
            rule=outcome.rule,
            decision=outcome.decision.value,
            tool=tool,
            args=args,
            risk=self._risk_of(tool),
            taint=ctx.taint.value,
            approver=outcome.approval.approver if outcome.approval else "-",
        )
        self.audit.record(
            "gate.decision",
            session=ctx.session_id,
            tool=tool,
            args=args,
            decision=outcome.decision.value,
            rule=outcome.rule,
            reason=outcome.reason,
            taint=ctx.taint.value,
            approver=outcome.approval.approver if outcome.approval else "",
        )
        return outcome

    def _decide(self, tool: str, args: Dict[str, Any], ctx: SessionContext) -> GateOutcome:
        # 规则 1：kill switch
        if self.killed:
            return self._deny(ctx, "运行时已被冻结：{}".format(self.kill_reason), "gate.kill_switch")

        spec = self.registry.get(tool)
        # 规则 2：default-deny（未注册工具，包含被投毒的 MCP 影子工具）
        if spec is None:
            return self._deny(
                ctx, "工具 {} 不在 allowlist（default-deny）".format(tool), "gate.unregistered_tool"
            )

        # 规则 3：参数级确定性校验（异常即不通过）
        for name, rule in spec.arg_rules.items():
            if name not in args or not rule.holds(args[name]):
                return self._deny(
                    ctx,
                    "参数 {} 未通过校验：{}（实际 {!r}）".format(name, rule.describe, args.get(name)),
                    "gate.arg_rule",
                )

        # 规则 4：单工具配额
        used = ctx.tool_calls.get(tool, 0)
        if used >= spec.max_calls_per_run:
            return self._deny(
                ctx,
                "工具 {} 已达单会话上限 {} 次".format(tool, spec.max_calls_per_run),
                "gate.tool_quota",
            )

        # 规则 5：风险分级
        if spec.risk is Risk.LOW:
            return GateOutcome(Verdict(Decision.ALLOW, "低风险只读动作，放行", "gate.risk_low"))
        if spec.risk is Risk.MEDIUM:
            return GateOutcome(Verdict(Decision.ALLOW, "中风险可逆动作，限流并全程留痕", "gate.risk_medium"))

        # 规则 6：污点会话中的高危外发 —— 不给审批机会，直接拒绝
        if ctx.tainted:
            return self._deny(
                ctx,
                "会话已被不可信内容污染（来源 {}），高危动作直接拒绝：注入不得转化为外部动作".format(
                    "、".join(ctx.taint_sources) or "未知"
                ),
                "gate.tainted_high_risk",
            )

        # 规则 7：同参数动作已获批过 —— 复用，不重复打扰人
        digest = _action_digest(tool, args)
        if digest in ctx.approved_actions:
            return GateOutcome(Verdict(Decision.ALLOW, "复用本会话已审批的同参数动作", "gate.approved_cache"))

        # 规则 8：无审批通道 → fail-closed
        if self.approver is None:
            return self._deny(ctx, "高危动作但未配置审批通道（fail-closed）", "gate.no_approver")

        request = ApprovalRequest(
            session=ctx.session_id,
            tool=tool,
            args=args,
            risk=spec.risk,
            goal=ctx.goal,
            why="风险等级 {}：不可逆/对外动作，需人工确认".format(spec.risk.value),
        )
        obs.event(
            "gate.approval.request",
            "请求人工审批：{}".format(request.render()),
            level=logging.WARNING,
            tool=tool,
            args=args,
            risk=spec.risk.value,
        )
        decision = self.approver(request)
        if decision.approved:
            ctx.approved_actions.add(digest)
            ctx.approvals_granted += 1
            self.audit.record(
                "gate.approval",
                session=ctx.session_id,
                tool=tool,
                args=args,
                result="approved",
                approver=decision.approver,
                note=decision.note,
            )
            return GateOutcome(
                Verdict(Decision.ALLOW, "人工审批通过（{}）".format(decision.note), "gate.approval_approved"),
                approval=decision,
            )
        ctx.approvals_refused += 1
        self.audit.record(
            "gate.approval",
            session=ctx.session_id,
            tool=tool,
            args=args,
            result="rejected",
            approver=decision.approver,
            note=decision.note,
        )
        obs.event(
            "gate.approval.result",
            "人工审批被拒绝",
            level=logging.WARNING,
            tool=tool,
            approver=decision.approver,
            note=decision.note,
        )
        return GateOutcome(
            Verdict(Decision.DENY, "人工审批被拒绝（{}）".format(decision.note), "gate.approval_rejected"),
            approval=decision,
        )

    def _deny(self, ctx: SessionContext, reason: str, rule: str) -> GateOutcome:
        ctx.denials += 1
        return GateOutcome(Verdict(Decision.DENY, reason, rule))

    def _risk_of(self, tool: str) -> str:
        spec = self.registry.get(tool)
        return spec.risk.value if spec else "unknown"

    # ── 执行后回写 ───────────────────────────────────────────────────────────
    def observe(self, outcome: ToolOutcome, ctx: SessionContext) -> Dict[str, Any]:
        """工具执行后回写：计数 + 污点标记。返回本次观察的结构化摘要（供日志/结果）。"""
        spec = self.registry.get(outcome.tool)
        ctx.tool_calls[outcome.tool] = ctx.tool_calls.get(outcome.tool, 0) + 1
        summary: Dict[str, Any] = {
            "tool": outcome.tool,
            "ok": outcome.ok,
            "duration_ms": outcome.duration_ms,
            "output_chars": len(outcome.observation()),
        }
        if not outcome.ok:
            obs.event(
                "tool.error",
                "工具执行失败：{}".format(outcome.error),
                level=logging.WARNING,
                tool=outcome.tool,
                error=outcome.error,
            )
        if spec is not None and spec.produces_untrusted:
            first = ctx.mark_tainted(outcome.tool)
            summary["tainted"] = True
            obs.event(
                "taint.mark",
                "不可信内容进入上下文：{}（首次={}）".format(outcome.tool, first),
                level=logging.WARNING,
                tool=outcome.tool,
                first=first,
                taint_sources=ctx.taint_sources,
            )
            self.audit.record("taint.mark", session=ctx.session_id, source=outcome.tool, first=first)
        self.audit.record("tool.exec", session=ctx.session_id, **summary)
        return summary
