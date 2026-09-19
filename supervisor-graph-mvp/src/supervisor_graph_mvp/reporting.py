"""Reporting: the ruling as text for a human, and as JSON for a machine.

Two renderers over the same object, no logic of their own:

* :func:`render_ruling` — the Markdown a reviewer reads (verdict first, then the
  evidence trail, then the consistency findings and the narrative);
* :func:`result_payload` — the ``result.json`` contract: intent, plan, every
  specialist output, the findings and the ruling. Field names are stable and the
  tests assert them, because the HTML report and any future UI read this file.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from . import state as st

VERDICT_LABELS = {
    "allow": "放行",
    "review": "审批放行",
    "block": "阻断",
    "escalate": "转人工复核",
}

STATUS_LABELS = {
    "ok": "已完成",
    "needs_review": "待审批",
    "escalated": "已升级人工",
    "failed": "失败",
}

SEVERITY_MARKS = {"blocking": "🛑", "warning": "⚠️"}


def render_ruling(
    ruling: Mapping[str, Any], *, findings: List[Mapping[str, Any]] | None = None
) -> str:
    """Render the ruling as Markdown (the CLI's default human output)."""
    verdict = str(ruling.get("verdict") or "")
    lines: List[str] = [
        "## 合规裁决：{}（{}）".format(
            verdict or "未定", VERDICT_LABELS.get(verdict, verdict or "未定")
        ),
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        "| 资产级别 | {} |".format(ruling.get("level") or "未定级"),
        "| 策略决策 | {} |".format(ruling.get("decision") or "无"),
        "| 一致性 | {} |".format(
            "通过"
            if (ruling.get("consistency") or {}).get("passed")
            else "未通过（{} 项阻断）".format((ruling.get("consistency") or {}).get("blocking", 0))
        ),
        "| 参与 Agent | {} |".format("、".join(ruling.get("agents") or []) or "无"),
        "",
    ]
    matched = ruling.get("matched_rules") or []
    if matched:
        lines.append("### 命中规则")
        lines.append("")
        lines.append("| 规则 | 名称 | 决策 | 阈值 | 责任方 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for rule in matched:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    rule.get("id"),
                    rule.get("name"),
                    rule.get("decision"),
                    rule.get("threshold_level"),
                    rule.get("owner"),
                )
            )
        lines.append("")
    actions = ruling.get("actions") or []
    if actions:
        lines.append("### 处置动作")
        lines.append("")
        lines.append("| 动作 | 类型 | 责任方 | 时限(h) |")
        lines.append("| --- | --- | --- | --- |")
        for action in actions:
            lines.append(
                "| {} | {} | {} | {} |".format(
                    action.get("name"),
                    action.get("kind"),
                    action.get("owner"),
                    action.get("sla_hours"),
                )
            )
        lines.append("")
    evidence = ruling.get("evidence") or {}
    if evidence.get("verified") or evidence.get("unverified"):
        lines.append("### 证据")
        lines.append("")
        for item in evidence.get("verified") or []:
            lines.append("- ✅ {}".format(item.get("id")))
        for item in evidence.get("unverified") or []:
            lines.append("- ❌ {}（{}）".format(item.get("id"), item.get("reason")))
        lines.append("")
    findings = list(findings if findings is not None else ruling.get("findings") or [])
    if findings:
        lines.append("### 一致性问题")
        lines.append("")
        for finding in findings:
            mark = SEVERITY_MARKS.get(str(finding.get("severity")), "·")
            lines.append("- {} [{}] {}".format(mark, finding.get("rule"), finding.get("message")))
        lines.append("")
    if ruling.get("narrative"):
        lines.append("### 结论说明（{}）".format(ruling.get("narrative_source") or "unknown"))
        lines.append("")
        lines.append(str(ruling.get("narrative")))
        lines.append("")
    escalation = ruling.get("escalation")
    if escalation:
        lines.append("### 升级人工")
        lines.append("")
        lines.append("- 原因：{}".format(escalation.get("reason")))
        lines.append("- 待人工处理：{}".format(escalation.get("asked_of_human")))
        lines.append("")
    return "\n".join(lines)


def result_payload(run_state: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``result.json`` contract — everything a reviewer or a report needs."""
    graph = run_state.get(st.KEY_GRAPH) or {}
    return {
        "request": dict(run_state.get(st.KEY_REQUEST) or {}),
        "options": dict(run_state.get(st.KEY_OPTIONS) or {}),
        "intent": dict(run_state.get(st.KEY_INTENT) or {}),
        "plan": dict(run_state.get(st.KEY_PLAN) or {}),
        "results": dict(run_state.get(st.KEY_RESULTS) or {}),
        "findings": list(run_state.get(st.KEY_FINDINGS) or []),
        "consistency": dict(run_state.get(st.KEY_CONSISTENCY) or {}),
        "ruling": dict(run_state.get(st.KEY_RULING) or {}),
        "narrative": dict(run_state.get(st.KEY_NARRATIVE) or {}),
        "verdict": run_state.get(st.KEY_VERDICT) or "",
        "status": run_state.get(st.KEY_STATUS) or "",
        "repairs": int(run_state.get(st.KEY_REPAIRS) or 0),
        "repair_log": list(run_state.get(st.KEY_REPAIR_LOG) or []),
        "llm_calls": [call for call in (run_state.get(st.KEY_LLM_CALLS) or []) if call],
        "graph": dict(graph),
        "visits": list(run_state.get(st.KEY_VISITS) or []),
    }


def summarize(run_state: Mapping[str, Any]) -> str:
    """One-line outcome summary for the console footer."""
    verdict = str(run_state.get(st.KEY_VERDICT) or "未定")
    status = str(run_state.get(st.KEY_STATUS) or "")
    graph = run_state.get(st.KEY_GRAPH) or {}
    return "{} ({}) · {} 步 · 修复 {} 轮 · 一致性问题 {} 项".format(
        verdict,
        STATUS_LABELS.get(status, status),
        graph.get("steps", 0),
        run_state.get(st.KEY_REPAIRS) or 0,
        len(run_state.get(st.KEY_FINDINGS) or []),
    )


__all__ = [
    "SEVERITY_MARKS",
    "STATUS_LABELS",
    "VERDICT_LABELS",
    "render_ruling",
    "result_payload",
    "summarize",
]
