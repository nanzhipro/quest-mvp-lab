"""The execution spine: a minimal, explicit state graph (nodes, edges, reducers, visits).

This is the *second* of the design's two pieces. It is deliberately boring: a node
is a callable returning a partial update, an edge says where to go next, and one
shared state dict is threaded through with per-key reducers. What it adds over a
plain ``while`` loop is the four things the design needs and a bare loop cannot
express:

* **conditional edges** — after the consistency check the graph either repairs,
  finalises, or escalates, and that decision is a pure function of the state;
* **cycles** — the repair edge ``check → dispatch`` is a real cycle, bounded by a
  per-node visit cap rather than by discipline;
* **a visit log** — every node entry/exit with duration, update keys and the chosen
  successor, which is what the evidence run and the HTML report are built from;
* **state reducers** — appends (findings, visits) and merges (results) instead of
  every node having to re-read and re-write whole collections.

Deliberately *not* here: fan-out, parallel branches, streaming, persistence. A
sub-task DAG is executed *inside* one node (``dispatch``) because the Supervisor's
routing — not the graph — is what orders the specialists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

END = "__end__"

State = Dict[str, Any]
NodeFn = Callable[[Mapping[str, Any]], Optional[Mapping[str, Any]]]
RouterFn = Callable[[Mapping[str, Any]], str]
Reducer = Callable[[Any, Any], Any]


class GraphError(RuntimeError):
    """The graph itself is malformed (unknown node, unreachable node, ambiguous edge)."""


def replace_reducer(_current: Any, update: Any) -> Any:
    """Default reducer: the new value wins."""
    return update


def append_reducer(current: Any, update: Any) -> Any:
    """List reducer: extend in place-order (findings, visits, llm call digests)."""
    return list(current or []) + list(update or [])


def merge_reducer(current: Any, update: Any) -> Any:
    """Mapping reducer: shallow merge (sub-task results keyed by id)."""
    merged = dict(current or {})
    merged.update(update or {})
    return merged


@dataclass(frozen=True)
class NodeSpec:
    """A node's public face: its function and a one-line human description."""

    name: str
    fn: NodeFn
    description: str = ""


@dataclass(frozen=True)
class NodeVisit:
    """One node execution — the atom of the evidence trail."""

    node: str
    step: int
    duration_ms: int
    updated_keys: Tuple[str, ...]
    next_node: str
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "step": self.step,
            "duration_ms": self.duration_ms,
            "updated_keys": list(self.updated_keys),
            "next_node": self.next_node,
            "error": self.error,
        }


@dataclass
class _Edge:
    """Either a static successor or a router plus its optional key→node mapping."""

    target: Optional[str] = None
    router: Optional[RouterFn] = None
    mapping: Dict[str, str] = field(default_factory=dict)


class StateGraph:
    """Build-time API: declare nodes and edges, then ``compile()``.

    Static edges are single-target on purpose: a graph that fans out silently hides
    where the work order is decided. Cycles are allowed, and capped per node.
    """

    def __init__(
        self,
        name: str = "orchestration",
        *,
        reducers: Optional[Mapping[str, Reducer]] = None,
        strict: bool = True,
    ) -> None:
        self.name = name
        self.reducers: Dict[str, Reducer] = dict(reducers or {})
        self.strict = strict
        self._nodes: Dict[str, NodeSpec] = {}
        self._order: List[str] = []
        self._edges: Dict[str, _Edge] = {}
        self._entry: Optional[str] = None

    # ── build API ─────────────────────────────────────────────────────────────
    def add_node(self, name: str, fn: NodeFn, *, description: str = "") -> "StateGraph":
        if name in self._nodes:
            raise GraphError("node {!r} is already defined".format(name))
        if name == END:
            raise GraphError("{!r} is reserved for the terminal state".format(END))
        self._nodes[name] = NodeSpec(name=name, fn=fn, description=description)
        self._order.append(name)
        return self

    def add_edge(self, source: str, target: str) -> "StateGraph":
        self._guard_source(source)
        if source in self._edges:
            raise GraphError(
                "node {!r} already has an outgoing edge; use add_conditional_edges".format(source)
            )
        if target != END:
            self._guard_node(target)
        self._edges[source] = _Edge(target=target)
        return self

    def add_conditional_edges(
        self,
        source: str,
        router: RouterFn,
        mapping: Optional[Mapping[str, str]] = None,
    ) -> "StateGraph":
        """Route out of ``source`` by asking ``router(state)``.

        The router returns either a node name (or ``END``) directly, or a key that
        ``mapping`` translates. Both are validated at compile time so a typo is a
        build error rather than a runtime surprise.
        """
        self._guard_source(source)
        if source in self._edges:
            raise GraphError(
                "node {!r} already has an outgoing edge; one successor source only".format(source)
            )
        resolved: Dict[str, str] = {}
        for key, target in dict(mapping or {}).items():
            if target != END:
                self._guard_node(target)
            resolved[str(key)] = target
        self._edges[source] = _Edge(router=router, mapping=resolved)
        return self

    def set_entry(self, name: str) -> "StateGraph":
        self._guard_node(name)
        self._entry = name
        return self

    # ── introspection ─────────────────────────────────────────────────────────
    @property
    def nodes(self) -> Tuple[str, ...]:
        return tuple(self._order)

    @property
    def entry(self) -> Optional[str]:
        return self._entry

    def node_description(self, name: str) -> str:
        return self._nodes[name].description

    def outgoing(self, name: str) -> Tuple[str, ...]:
        """Successor *nodes* of ``name`` (END excluded — a node with none ends the graph)."""
        edge = self._edges.get(name)
        if edge is None:
            return ()
        if edge.target is not None:
            return () if edge.target == END else (edge.target,)
        return tuple(dict.fromkeys(target for target in edge.mapping.values() if target != END))

    def is_conditional(self, name: str) -> bool:
        edge = self._edges.get(name)
        return bool(edge and edge.router is not None)

    def to_mermaid(self) -> str:
        """Render the graph as Mermaid ``flowchart TD`` source (structure only)."""
        lines = ["flowchart TD", "    START([start]) --> {}".format(self._entry or "?")]
        for name in self._order:
            label = self._nodes[name].description or name
            lines.append('    {}["{}<br/><small>{}</small>"]'.format(name, name, label))
        for name in self._order:
            edge = self._edges.get(name)
            if edge is None:
                lines.append("    {} --> DONE([end])".format(name))
                continue
            if edge.target is not None:
                lines.append(
                    "    {} --> {}".format(
                        name, "DONE([end])" if edge.target == END else edge.target
                    )
                )
                continue
            for key, target in edge.mapping.items():
                label = "end" if key == END else key
                node = "DONE([end])" if target == END else target
                lines.append("    {} -->|{}| {}".format(name, label, node))
        return "\n".join(lines)

    # ── compile ───────────────────────────────────────────────────────────────
    def compile(self, *, max_visits_per_node: int = 8) -> "CompiledGraph":
        if self._entry is None:
            raise GraphError("graph has no entry node; call set_entry()")
        for name in self._order:
            if name not in self._edges:
                raise GraphError(
                    "node {!r} has no outgoing edge (use END as its target)".format(name)
                )
        if self.strict:
            reachable = self._reachable()
            dead = [name for name in self._order if name not in reachable]
            if dead:
                raise GraphError("unreachable node(s) from entry: {}".format(", ".join(dead)))
        return CompiledGraph(graph=self, max_visits_per_node=max(1, int(max_visits_per_node)))

    def _reachable(self) -> set:
        # A conditional edge declared without a mapping can route to *any* node, so a
        # dead-node check would be a guess: fall back to "everything is reachable" and
        # let the dispatch-time validation catch a bad target instead.
        if any(edge.router is not None and not edge.mapping for edge in self._edges.values()):
            return set(self._nodes)
        seen = set()
        stack = [self._entry] if self._entry else []
        while stack:
            name = stack.pop()
            if name in seen or name == END or name not in self._nodes:
                continue
            seen.add(name)
            stack.extend(self.outgoing(name))
        return seen

    def _guard_node(self, name: str) -> None:
        if name not in self._nodes:
            raise GraphError("unknown node {!r}".format(name))

    def _guard_source(self, name: str) -> None:
        self._guard_node(name)


class CompiledGraph:
    """Run-time API: thread one state dict through the nodes until ``END``."""

    def __init__(self, graph: StateGraph, *, max_visits_per_node: int = 8) -> None:
        self.graph = graph
        self.max_visits_per_node = max_visits_per_node

    def resolve(self, node: str, state: Mapping[str, Any]) -> str:
        """Where does the graph go after ``node``? Pure, so it is directly testable."""
        edge = self.graph._edges[node]
        if edge.router is None:
            return edge.target or END
        decision = edge.router(state)
        if decision in edge.mapping:
            return edge.mapping[decision]
        if decision == END:
            return END
        if decision in self.graph._nodes:
            return decision
        raise GraphError(
            "router after {!r} returned {!r}, which is neither a mapped key ({}) nor a node".format(
                node, decision, ", ".join(sorted(edge.mapping)) or "none"
            )
        )

    def invoke(
        self,
        state: State,
        *,
        max_steps: int = 24,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> State:
        """Run the graph. Mutates and returns ``state`` (one state object per run).

        Stops on ``END``, on the step budget, or on the per-node visit cap — the
        last two land in ``state['graph']['stopped_reason']`` so a truncated run can
        never be mistaken for a clean one.
        """
        emit = on_event or (lambda _event, _payload: None)
        visits = state.setdefault("visits", [])
        current = self.graph._entry or END
        counted: Dict[str, int] = {}
        stopped = "end"
        step = 0
        while current != END:
            if step >= max_steps:
                stopped = "step_budget"
                break
            counted[current] = counted.get(current, 0) + 1
            if counted[current] > self.max_visits_per_node:
                stopped = "visit_budget:{}".format(current)
                break
            step += 1
            spec = self.graph._nodes[current]
            started = time.perf_counter()
            error = ""
            try:
                update = spec.fn(state) or {}
            except Exception as exc:
                update = {}
                error = "{}: {}".format(type(exc).__name__, exc)
                emit("node_error", {"node": current, "step": step, "error": error})
            duration_ms = int((time.perf_counter() - started) * 1000)
            for key, value in update.items():
                reducer = self.graph.reducers.get(key, replace_reducer)
                state[key] = reducer(state.get(key), value)
            next_node = self.resolve(current, state) if not error else END
            visit = NodeVisit(
                node=current,
                step=step,
                duration_ms=duration_ms,
                updated_keys=tuple(sorted(update)),
                next_node=next_node,
                error=error,
            )
            visits.append(visit.as_dict())
            emit("node_exit", visit.as_dict())
            current = next_node
        state["graph"] = {
            "name": self.graph.name,
            "steps": step,
            "stopped_reason": stopped,
            "visits": len(visits),
            "node_visits": dict(sorted(counted.items())),
        }
        emit("graph_stop", dict(state["graph"]))
        return state

    def describe(self) -> List[Dict[str, Any]]:
        """Node/edge inventory for the CLI ``graph`` command and the report."""
        rows: List[Dict[str, Any]] = []
        for name in self.graph.nodes:
            rows.append(
                {
                    "node": name,
                    "description": self.graph.node_description(name),
                    "successors": list(self.graph.outgoing(name)),
                    "conditional": self.graph.is_conditional(name),
                }
            )
        return rows

    def mermaid(self) -> str:
        return self.graph.to_mermaid()


def validate_dag(
    subtasks: Sequence[Mapping[str, Any]],
    known_agents: Sequence[str],
) -> List[str]:
    """Check a Supervisor-produced sub-task plan. Returns human-readable problems.

    Four ways a model-produced plan goes wrong, all caught here rather than at
    dispatch time: duplicate ids, unknown agents, dangling ``depends_on``, cycles.
    The Supervisor turns these problems into one repair round, then into its
    deterministic template plan.
    """
    problems: List[str] = []
    ids: List[str] = []
    for index, task in enumerate(subtasks, start=1):
        if not isinstance(task, Mapping):
            problems.append("sub-task #{} is not an object".format(index))
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            problems.append("sub-task #{} has no id".format(index))
            continue
        if task_id in ids:
            problems.append("duplicate sub-task id {!r}".format(task_id))
        ids.append(task_id)
        agent = str(task.get("agent") or "").strip()
        if not agent:
            problems.append("sub-task {!r} names no agent".format(task_id))
        elif agent not in known_agents:
            problems.append(
                "sub-task {!r} names unknown agent {!r} (known: {})".format(
                    task_id, agent, ", ".join(known_agents)
                )
            )
        deps = task.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            problems.append("sub-task {!r} has a non-list depends_on".format(task_id))
            continue
        for dep in deps:
            if not isinstance(dep, str):
                problems.append("sub-task {!r} depends on a non-string entry".format(task_id))
            elif dep == task_id:
                problems.append("sub-task {!r} depends on itself".format(task_id))
    known = set(ids)
    for task in subtasks:
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("id") or "").strip()
        deps = task.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            continue
        for dep in deps:
            if isinstance(dep, str) and dep not in known:
                problems.append("sub-task {!r} depends on missing {!r}".format(task_id, dep))
    problems.extend(_cycle_problems(subtasks))
    return problems


def _cycle_problems(subtasks: Sequence[Mapping[str, Any]]) -> List[str]:
    """Peel off dependency-free sub-tasks; whatever survives is inside a cycle."""
    graph: Dict[str, List[str]] = {}
    for task in subtasks:
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("id") or "").strip()
        deps = task.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        graph[task_id] = [dep for dep in deps if isinstance(dep, str)]
    remaining = {node: [dep for dep in deps if dep in graph] for node, deps in graph.items()}
    progress = True
    while progress:
        progress = False
        for node in list(remaining):
            if all(dep not in remaining for dep in remaining[node]):
                del remaining[node]
                progress = True
    if not remaining:
        return []
    return ["dependency cycle: {}".format(" → ".join(_find_cycle(remaining)))]


def _find_cycle(remaining: Dict[str, List[str]]) -> List[str]:
    """Walk dependencies from one surviving node until a node repeats."""
    node = sorted(remaining)[0]
    order: List[str] = []
    seen: Dict[str, int] = {}
    while node not in seen:
        seen[node] = len(order)
        order.append(node)
        deps = [dep for dep in remaining.get(node, []) if dep in remaining]
        node = deps[0] if deps else order[0]
    return [*order[seen[node] :], node]
