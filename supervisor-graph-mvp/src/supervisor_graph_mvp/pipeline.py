"""The pipeline: how the two pieces are wired into one running system.

The graph is small on purpose — nine nodes, one cycle:

```
intake → classify → plan → dispatch → check ─┬─ repair ──→ dispatch   (the cycle)
                                             ├─ clean  ──→ aggregate → END
                                             └─ stuck  ──→ escalate  → END
```

Reading it is reading the design: the Supervisor's four duties are nodes 2, 3, 4 and
(part of) 5; the *state graph* is what turns "check the specialists' work" into a
control-flow decision instead of a hope. ``check`` is the only node allowed to send
the run backwards, and it can only do so while the repair budget lasts — after that
the run escalates instead of quietly declaring success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import state as st
from .agents.registry import AgentRegistry, default_registry
from .config import DEFAULT_MAX_REPAIRS, DEFAULT_MAX_SUPERSTEPS
from .consistency import ConsistencyChecker
from .graph import END, CompiledGraph, StateGraph
from .llm import ChatModel
from .supervisor import Supervisor

#: Node names, exposed so tests and the HTML report can address them without magic strings.
NODE_INTAKE = "intake"
NODE_CLASSIFY = "classify"
NODE_PLAN = "plan"
NODE_DISPATCH = "dispatch"
NODE_CHECK = "check"
NODE_AGGREGATE = "aggregate"
NODE_ESCALATE = "escalate"

NODE_HELP = {
    NODE_INTAKE: "请求归一化（补齐默认字段）",
    NODE_CLASSIFY: "Supervisor 职责一：意图识别",
    NODE_PLAN: "Supervisor 职责二：任务规划（子任务 DAG）",
    NODE_DISPATCH: "Supervisor 职责三：路由派发（按依赖顺序执行）",
    NODE_CHECK: "一致性检查（决定修复 / 收口 / 升级）",
    NODE_AGGREGATE: "Supervisor 职责四：结果聚合与叙述",
    NODE_ESCALATE: "升级人工复核（证据无法自洽）",
}


class Orchestrator:
    """Assembles the graph and runs one request through it."""

    def __init__(
        self,
        model: ChatModel,
        registry: Optional[AgentRegistry] = None,
        *,
        max_repairs: int = DEFAULT_MAX_REPAIRS,
        max_supersteps: int = DEFAULT_MAX_SUPERSTEPS,
        tracer: Optional[Any] = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.supervisor = Supervisor(model, self.registry, max_repairs=max_repairs)
        self.checker = ConsistencyChecker(agent_names=self.registry.names)
        self.max_repairs = max(0, int(max_repairs))
        self.max_supersteps = max(1, int(max_supersteps))
        self.tracer = tracer
        self.model = model
        self._graph: Optional[CompiledGraph] = None

    # ── graph construction ────────────────────────────────────────────────────
    def build(self) -> StateGraph:
        """Declare nodes and edges. Called by :meth:`compile`; kept public for tests."""
        graph = StateGraph("compliance-orchestration", reducers=st.REDUCERS)
        graph.add_node(NODE_INTAKE, self._node_intake, description=NODE_HELP[NODE_INTAKE])
        graph.add_node(NODE_CLASSIFY, self._node_classify, description=NODE_HELP[NODE_CLASSIFY])
        graph.add_node(NODE_PLAN, self._node_plan, description=NODE_HELP[NODE_PLAN])
        graph.add_node(NODE_DISPATCH, self._node_dispatch, description=NODE_HELP[NODE_DISPATCH])
        graph.add_node(NODE_CHECK, self._node_check, description=NODE_HELP[NODE_CHECK])
        graph.add_node(NODE_AGGREGATE, self._node_aggregate, description=NODE_HELP[NODE_AGGREGATE])
        graph.add_node(NODE_ESCALATE, self._node_escalate, description=NODE_HELP[NODE_ESCALATE])
        graph.set_entry(NODE_INTAKE)
        graph.add_edge(NODE_INTAKE, NODE_CLASSIFY)
        graph.add_edge(NODE_CLASSIFY, NODE_PLAN)
        graph.add_edge(NODE_PLAN, NODE_DISPATCH)
        graph.add_edge(NODE_DISPATCH, NODE_CHECK)
        graph.add_conditional_edges(
            NODE_CHECK,
            lambda run_state: str(
                (run_state.get(st.KEY_CONSISTENCY) or {}).get("decision") or "finalize"
            ),
            {"repair": NODE_DISPATCH, "finalize": NODE_AGGREGATE, "escalate": NODE_ESCALATE},
        )
        graph.add_edge(NODE_AGGREGATE, END)
        graph.add_edge(NODE_ESCALATE, END)
        return graph

    def compile(self) -> CompiledGraph:
        if self._graph is None:
            self._graph = self.build().compile(max_visits_per_node=max(2, self.max_repairs + 2))
        return self._graph

    @property
    def graph(self) -> CompiledGraph:
        return self.compile()

    # ── nodes ─────────────────────────────────────────────────────────────────
    def _emit(self, event: str, payload: Mapping[str, Any]) -> None:
        if self.tracer is not None:
            self.tracer.emit(event, dict(payload))

    def _node_intake(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        """Normalise the request so every later node sees the same field set."""
        request = dict(run_state.get(st.KEY_REQUEST) or {})
        asset = dict(request.get("asset") or {})
        asset.setdefault("kind", "未知")
        asset.setdefault("channel", "未知")
        asset.setdefault("destination_type", "external")
        asset.setdefault("owner", "未知")
        request["asset"] = asset
        request.setdefault("evidence", [])
        request.setdefault("id", "ad-hoc")
        request.setdefault("text", "")
        return {"request": request, "status": st.STATUS_OK}

    def _node_classify(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        intent = self.supervisor.classify(run_state.get(st.KEY_REQUEST) or {})
        self._emit("intent", {"intent": intent})
        return {"intent": intent, "llm_calls": [intent.get("llm") or {}]}

    def _node_plan(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        plan = self.supervisor.plan(
            run_state.get(st.KEY_REQUEST) or {}, run_state.get(st.KEY_INTENT) or {}
        )
        self._emit("plan", {"plan": plan})
        return {"plan": plan, "llm_calls": [plan.get("llm") or {}]}

    def _node_dispatch(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        """Route: either the first pass over the plan, or the repair round named by check."""
        repairs = list(run_state.get(st.KEY_PENDING_REPAIRS) or [])
        round_index = int(run_state.get(st.KEY_REPAIRS) or 0) + (1 if repairs else 0)
        update = self.supervisor.route(run_state, repairs=repairs or None, round_index=round_index)
        if repairs:
            update[st.KEY_REPAIRS] = round_index
        update[st.KEY_PENDING_REPAIRS] = []
        for task_id, record in (update.get("results") or {}).items():
            self._emit(
                "subtask",
                {
                    "subtask": task_id,
                    "agent": record.get("agent"),
                    "ok": record.get("ok"),
                    "attempt": record.get("attempt"),
                    "hint": record.get("hint"),
                    "duration_ms": record.get("duration_ms"),
                    "status": (record.get("output") or {}).get("status"),
                },
            )
        return update

    def _node_check(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        """Run the rule set and decide the graph's next move."""
        outcome = self.checker.check(run_state)
        findings = outcome["findings"]
        report = dict(outcome["report"])
        blocking = [finding for finding in findings if finding.get("severity") == "blocking"]
        budget_left = self.max_repairs - int(run_state.get(st.KEY_REPAIRS) or 0)
        repairs = st.repair_targets(blocking)
        if blocking and budget_left > 0 and repairs:
            decision = "repair"
        elif blocking:
            decision = "escalate"
        else:
            decision = "finalize"
        report["decision"] = decision
        report["repair_budget_left"] = budget_left
        report["next_repairs"] = repairs
        self._emit(
            "consistency",
            {
                "passed": report["passed"],
                "blocking": report["blocking"],
                "warnings": report["warnings"],
                "decision": decision,
                "findings": findings,
            },
        )
        return {"findings": findings, "consistency": report, "pending_repairs": repairs}

    def _node_aggregate(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        update = self.supervisor.aggregate(run_state)
        verdict = str(update.get("verdict") or "")
        status = st.STATUS_NEEDS_REVIEW if verdict == "review" else st.STATUS_OK
        self._emit("ruling", {"ruling": update.get("ruling"), "status": status})
        return {**update, "status": status}

    def _node_escalate(self, run_state: Mapping[str, Any]) -> Dict[str, Any]:
        """A run that cannot reconcile its own evidence ends here — with a human on the hook."""
        update = self.supervisor.aggregate(run_state, narrate=False)
        blocking = [
            finding
            for finding in (run_state.get(st.KEY_FINDINGS) or [])
            if finding.get("severity") == "blocking"
        ]
        ruling = dict(update.get("ruling") or {})
        ruling["verdict"] = "escalate"
        ruling["escalation"] = {
            "reason": "一致性检查在修复预算内无法收口，转人工复核",
            "blocking_findings": blocking,
            "next_repairs": (run_state.get(st.KEY_CONSISTENCY) or {}).get("next_repairs") or [],
            "asked_of_human": "补齐或驳回缺失证据后重新提交请求",
        }
        self._emit("escalation", {"ruling": ruling, "blocking": len(blocking)})
        return {
            "ruling": ruling,
            "verdict": "escalate",
            "status": st.STATUS_ESCALATED,
        }

    # ── run ───────────────────────────────────────────────────────────────────
    def run(
        self,
        request: Mapping[str, Any],
        *,
        options: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run one request end to end; returns the final state (the run's whole truth)."""
        run_state = st.new_state(request, options)
        self._emit("run_start", {"request": dict(request), "graph": self.graph.graph.to_mermaid()})
        return self.graph.invoke(run_state, max_steps=self.max_supersteps, on_event=self._emit)

    def describe(self) -> List[Dict[str, Any]]:
        return self.graph.describe()

    def mermaid(self) -> str:
        return self.graph.mermaid()


def build_orchestrator(
    model: ChatModel,
    *,
    data_dir: Optional[Path] = None,
    registry: Optional[AgentRegistry] = None,
    max_repairs: int = DEFAULT_MAX_REPAIRS,
    max_supersteps: int = DEFAULT_MAX_SUPERSTEPS,
    tracer: Optional[Any] = None,
) -> Orchestrator:
    """Convenience factory used by the CLI (and by tests that want a fixture registry)."""
    registry = registry or default_registry(data_dir)
    return Orchestrator(
        model,
        registry,
        max_repairs=max_repairs,
        max_supersteps=max_supersteps,
        tracer=tracer,
    )


__all__ = [
    "NODE_AGGREGATE",
    "NODE_CHECK",
    "NODE_CLASSIFY",
    "NODE_DISPATCH",
    "NODE_ESCALATE",
    "NODE_HELP",
    "NODE_INTAKE",
    "NODE_PLAN",
    "Orchestrator",
    "build_orchestrator",
]
