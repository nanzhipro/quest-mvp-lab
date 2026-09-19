"""End-to-end pipeline tests: the five scripted scenarios and the graph's stop conditions.

These are the tests that say what the *system* does — which scenario takes the repair
edge, which one escalates, which one has a specialist synthesised for it, and how many
model calls each costs.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from supervisor_graph_mvp import state as st
from supervisor_graph_mvp.agents.base import AgentError
from supervisor_graph_mvp.agents.evidence import sha256_of
from supervisor_graph_mvp.agents.registry import AgentRegistry, default_registry
from supervisor_graph_mvp.demo import load_script
from supervisor_graph_mvp.llm import ScriptedClient
from supervisor_graph_mvp.pipeline import (
    NODE_AGGREGATE,
    NODE_CHECK,
    NODE_DISPATCH,
    NODE_ESCALATE,
    Orchestrator,
)
from supervisor_graph_mvp.scenarios import find_scenario, load_scenarios


# ── graph shape ───────────────────────────────────────────────────────────────
def test_graph_declares_the_documented_topology(data_dir):
    orchestrator = Orchestrator(ScriptedClient([]), default_registry(data_dir))
    rows = {row["node"]: row for row in orchestrator.describe()}
    assert set(rows) == {"intake", "classify", "plan", "dispatch", "check", "aggregate", "escalate"}
    assert rows["check"]["conditional"] is True
    assert set(rows["check"]["successors"]) == {NODE_DISPATCH, NODE_AGGREGATE, NODE_ESCALATE}
    assert rows["dispatch"]["successors"] == [NODE_CHECK]
    assert rows["aggregate"]["successors"] == []
    mermaid = orchestrator.mermaid()
    assert "check -->|repair| dispatch" in mermaid
    assert "check -->|finalize| aggregate" in mermaid
    assert "check -->|escalate| escalate" in mermaid


def test_every_node_has_a_human_readable_purpose(data_dir):
    orchestrator = Orchestrator(ScriptedClient([]), default_registry(data_dir))
    assert all(row["description"] for row in orchestrator.describe())


# ── the five scripted scenarios ───────────────────────────────────────────────
def test_s1_public_allow_goes_straight_through(run_scenario):
    run = run_scenario("S1-public-allow")
    assert run["verdict"] == "allow"
    assert run["status"] == st.STATUS_OK
    assert run["repairs"] == 0
    assert run["graph"]["node_visits"] == {
        "intake": 1,
        "classify": 1,
        "plan": 1,
        "dispatch": 1,
        "check": 1,
        "aggregate": 1,
    }
    assert run["ruling"]["level"] == "P1"
    assert run["ruling"]["narrative_source"] == "llm"
    assert [call["purpose"] for call in run["llm_calls"]] == ["classify", "plan", "narrative"]


def test_s2_plan_gap_is_repaired_by_the_cycle(run_scenario):
    run = run_scenario("S2-plan-gap")
    assert run["verdict"] == "review"
    assert run["status"] == st.STATUS_NEEDS_REVIEW
    assert run["repairs"] == 1
    assert run["graph"]["node_visits"]["dispatch"] == 2
    assert run["graph"]["node_visits"]["check"] == 2
    assert run["results"]["t4"]["attempt"] == 2
    assert [entry["agent"] for entry in run["repair_log"]] == ["remediation"]
    assert run["repair_log"][0]["synthesized"] is False
    assert run["findings"][0]["rule"] == "C7-contract"


def test_s3_missing_evidence_escalates_instead_of_guessing(run_scenario):
    run = run_scenario("S3-missing-evidence")
    assert run["verdict"] == "escalate"
    assert run["status"] == st.STATUS_ESCALATED
    assert run["graph"]["node_visits"][NODE_ESCALATE] == 1
    assert NODE_AGGREGATE not in run["graph"]["node_visits"]
    escalation = run["ruling"]["escalation"]
    assert escalation["blocking_findings"]
    assert (
        "审批单" in str(escalation["blocking_findings"][0]["detail"])
        or escalation["asked_of_human"]
    )
    assert run["ruling"]["narrative_source"] == "deterministic_template"
    # No narrative call on the escalation path: only classify + plan.
    assert [call["purpose"] for call in run["llm_calls"]] == ["classify", "plan"]


def test_s4_missing_specialist_is_synthesised(run_scenario):
    run = run_scenario("S4-block-p4")
    assert run["verdict"] == "block"
    assert run["status"] == st.STATUS_OK
    assert "r1-evidence" in run["results"]
    assert run["repair_log"][0]["synthesized"] is True
    assert run["repair_log"][0]["agent"] == "evidence"
    assert run["results"]["r1-evidence"]["attempt"] == 1
    kinds = {action["kind"] for action in run["ruling"]["actions"]}
    assert "block" in kinds


def test_s5_missing_producer_pulls_its_consumers_in_the_same_round(run_scenario):
    run = run_scenario("S5-plan-missing-agent")
    assert run["verdict"] == "review"
    assert run["repairs"] == 1
    assert run["repair_log"][0]["synthesized"] is True
    assert run["repair_log"][0]["agent"] == "classification"
    assert run["results"]["t2"]["attempt"] == 2  # policy retried once the level existed
    assert run["results"]["t3"]["attempt"] == 2  # remediation too
    assert all(result["output"]["status"] == "ok" for result in run["results"].values())


def test_every_packaged_scenario_has_a_script_and_a_verdict(run_scenario):
    scenarios = load_scenarios()
    assert len(scenarios) == 5
    for scenario in scenarios:
        assert load_script(str(scenario["id"]))
        run = run_scenario(str(scenario["id"]))
        assert run["verdict"]
        assert run["graph"]["stopped_reason"] == "end"


def test_repair_budget_zero_turns_a_repairable_run_into_an_escalation(sandbox):
    model = ScriptedClient(load_script("S4-block-p4"), label="S4-block-p4")
    orchestrator = Orchestrator(model, default_registry(sandbox), max_repairs=0)
    run = orchestrator.run(find_scenario("S4-block-p4"))
    assert run["verdict"] == "escalate"
    assert run["repairs"] == 0
    assert run["graph"]["node_visits"]["dispatch"] == 1


def test_a_dead_model_still_produces_a_ruling(sandbox, request_factory):
    """Every Supervisor duty has a deterministic fallback — including the plan."""
    digest = sha256_of(sandbox / "assets" / "finance-export-2026-0919.csv")
    orchestrator = Orchestrator(ScriptedClient([], label="dead"), default_registry(sandbox))
    run = orchestrator.run(
        request_factory(
            evidence=[
                {"id": "rule:R-03", "kind": "rule"},
                {
                    "id": "artifact:finance-export-2026-0919.csv",
                    "kind": "artifact",
                    "sha256": digest,
                },
            ]
        )
    )
    assert run["intent"]["source"] == "fallback"
    assert run["plan"]["source"] == "fallback"
    assert run["verdict"] == "review"
    assert run["ruling"]["narrative_source"] == "deterministic_template"
    assert all(call["error"] for call in run["llm_calls"])
    assert run["graph"]["stopped_reason"] == "end"


def test_the_step_budget_truncates_a_run_without_pretending_to_finish(sandbox, request_factory):
    orchestrator = Orchestrator(
        ScriptedClient([], label="dead"), default_registry(sandbox), max_supersteps=3
    )
    run = orchestrator.run(request_factory())
    assert run["graph"]["stopped_reason"] == "step_budget"
    assert run["ruling"] == {}
    assert run["verdict"] == ""


def test_a_broken_specialist_is_reported_not_raised(sandbox, request_factory):
    base = default_registry(sandbox)

    class Exploding(type(base.get("policy"))):
        name = "policy"

        def run(self, context):
            raise AgentError("boom")

    registry = AgentRegistry(
        agents=[Exploding() if name == "policy" else base.get(name) for name in base.names],
        data_dir=sandbox,
    )
    orchestrator = Orchestrator(ScriptedClient([]), registry)
    run = orchestrator.run(request_factory())
    policy_result = next(
        result for result in run["results"].values() if result["agent"] == "policy"
    )
    assert policy_result["ok"] is False
    assert "AgentError: boom" in policy_result["error"]
    assert run["verdict"] == "escalate"


def test_run_state_carries_the_evidence_trail(run_scenario):
    run = run_scenario("S4-block-p4")
    assert run["visits"] and all("node" in visit for visit in run["visits"])
    assert run["llm_calls"] and all(call["purpose"] for call in run["llm_calls"])
    assert run["intent"]["composite"] is True
    assert run["plan"]["source"] == "llm"
    assert run["artifacts"]


def test_options_are_recorded_for_evidence(run_scenario):
    model = ScriptedClient(load_script("S1-public-allow"))
    from supervisor_graph_mvp.scenarios import find_scenario as find

    orchestrator = Orchestrator(model, default_registry())
    run = orchestrator.run(find("S1-public-allow"), options={"mode": "test"})
    assert run[st.KEY_OPTIONS] == {"mode": "test"}


def test_tracer_receives_the_lifecycle_events(tmp_path: Path):
    from supervisor_graph_mvp.trace import Tracer

    tracer = Tracer(run_id="unit", trace_path=tmp_path / "trace.jsonl", console=False)
    orchestrator = Orchestrator(
        ScriptedClient(load_script("S1-public-allow")), default_registry(), tracer=tracer
    )
    orchestrator.run(find_scenario("S1-public-allow"))
    events: List[str] = [event["event"] for event in tracer.events]
    for expected in (
        "run_start",
        "node_exit",
        "intent",
        "plan",
        "subtask",
        "consistency",
        "ruling",
        "graph_stop",
    ):
        assert expected in events
    assert (tmp_path / "trace.jsonl").read_text(encoding="utf-8").count("\n") == len(tracer.events)
