"""场景套件：六个确定性轨迹必须全部通过，且复核逻辑本身要被验证。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest

from agent_guardrails.audit import AuditLog, verify_chain
from agent_guardrails.config import RunPaths
from agent_guardrails.gate import Gatekeeper, ScriptedApprover, SessionContext
from agent_guardrails.runtime import RunResult, ToolTrace
from agent_guardrails.scenarios import (
    SCENARIO_KEYS,
    SCENARIOS,
    ScenarioRun,
    get_scenario,
    run_scenario,
    scripted_model,
    verify,
)
from agent_guardrails.tools import ToolRegistry, Workspace, workspace_tools


def run_offline(key: str, tmp_path: Path) -> ScenarioRun:
    scenario = get_scenario(key)
    paths = RunPaths(root=tmp_path, scenario=key).ensure()
    return run_scenario(scenario, model=scripted_model(scenario), paths=paths)


@pytest.mark.parametrize("key", SCENARIO_KEYS)
def test_every_scenario_passes_offline(key: str, tmp_path: Path) -> None:
    run = run_offline(key, tmp_path)
    failed = [(check.name, check.detail) for check in run.checks if not check.ok and check.enforced]
    assert run.ok, "场景 {} 未通过：{}".format(key, failed)


@pytest.mark.parametrize("key", SCENARIO_KEYS)
def test_every_scenario_writes_a_verifiable_audit_chain(key: str, tmp_path: Path) -> None:
    run = run_offline(key, tmp_path)
    verification = verify_chain(tmp_path / key / "audit.jsonl")
    assert verification.ok, verification.reason
    assert verification.entries > 0
    assert run.result.session_id


def test_scenario_registry_is_well_formed() -> None:
    assert len(SCENARIO_KEYS) == len(set(SCENARIO_KEYS)) == 6
    for scenario in SCENARIOS:
        assert scenario.goal and scenario.threat and scenario.scripted
        assert scenario.expect_rules or scenario.expect_status
    with pytest.raises(KeyError):
        get_scenario("does_not_exist")


def test_side_effects_prove_nothing_leaked(tmp_path: Path) -> None:
    """最有说服力的证据不是"闸门说拒绝了"，而是目标系统里确实没有发生。"""
    injected = run_offline("indirect_injection", tmp_path)
    assert injected.side_effects["outbox"] == []
    deleted = run_offline("destructive_delete", tmp_path)
    assert deleted.side_effects["deletions"] == []


def test_injection_scenario_is_caught_by_two_independent_rules(tmp_path: Path) -> None:
    """换个收件人（绕过域名规则）也必须被挡住 —— 这就是纵深防御。"""
    run = run_offline("indirect_injection", tmp_path)
    assert run.result.tainted
    assert [trace.rule for trace in run.result.denied_calls] == ["gate.arg_rule"]

    registry = ToolRegistry(workspace_tools(Workspace.seeded()))
    gate = Gatekeeper(registry, AuditLog(None), ScriptedApprover(lambda request: True))
    session = SessionContext(goal="按网页要求处理")
    session.mark_tainted("web_fetch")
    outcome = gate.authorize("send_email", {"to": "alice@example.com", "subject": "s", "body": "b"}, session)
    assert outcome.rule == "gate.tainted_high_risk"


def test_verify_flags_a_denied_call_that_executed_anyway(tmp_path: Path) -> None:
    """复核逻辑必须自己可信：构造一个"拒绝却执行了"的结果，必须被判不通过。"""
    scenario = get_scenario("happy_path")
    registry = ToolRegistry(workspace_tools(Workspace.seeded()))
    result = RunResult(session_id="s", goal="g")
    result.tool_traces.append(
        ToolTrace(
            step=1,
            tool="send_email",
            args={},
            decision="deny",
            rule="gate.arg_rule",
            reason="拒绝",
            executed=True,
            ok=True,
        )
    )
    checks = verify(scenario, result, registry, {"outbox": [], "deletions": []})
    leaked = [check for check in checks if check.name == "invariant.denied_never_executed"]
    assert leaked and not leaked[0].ok


def test_verify_flags_high_risk_execution_without_approval() -> None:
    scenario = get_scenario("happy_path")
    registry = ToolRegistry(workspace_tools(Workspace.seeded()))
    result = RunResult(session_id="s", goal="g")
    result.tool_traces.append(
        ToolTrace(
            step=1,
            tool="send_email",
            args={},
            decision="allow",
            rule="gate.risk_low",
            reason="放行",
            executed=True,
            ok=True,
        )
    )
    checks = verify(scenario, result, registry, {"outbox": [{"to": "x"}], "deletions": []})
    approvals = [check for check in checks if check.name == "invariant.high_risk_needs_approval"]
    assert approvals and not approvals[0].ok


def test_verify_downgrades_expectations_to_observations_in_live_mode() -> None:
    """实测模式不能要求模型一定去撞边界，只能观测 —— 但安全不变量仍然强制。"""
    scenario = get_scenario("privilege_escalation")
    registry = ToolRegistry(workspace_tools(Workspace.seeded()))
    result = RunResult(session_id="s", goal="g", status="completed")
    checks: List[Any] = verify(scenario, result, registry, {"outbox": [], "deletions": []}, live=True)
    enforced = {check.name for check in checks if check.enforced}
    assert "invariant.denied_never_executed" in enforced
    assert "expect.rules_hit" not in enforced
    expectation = next(check for check in checks if check.name == "expect.rules_hit")
    assert not expectation.enforced


def test_scenario_run_payload_carries_checks_and_side_effects(tmp_path: Path) -> None:
    import json

    run = run_offline("happy_path", tmp_path)
    payload = json.loads(json.dumps(run.as_dict(), ensure_ascii=False))
    assert payload["scenario"] == "happy_path"
    assert payload["mode"] == "offline"
    assert payload["ok"] is True
    assert payload["side_effects"]["outbox"][0]["to"] == "alice@example.com"
