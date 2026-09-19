"""The shared state contract: key names, reducers, and the pure helpers over them.

The graph threads exactly one dict through the whole run. Keeping every key in one
place — and every derived view as a pure function of that dict — is what makes the
pipeline testable without a model, a network, or a clock.

Key groups:

* **inputs** — ``request``, ``options`` (immutable for the run);
* **Supervisor outputs** — ``intent``, ``plan``, ``ruling``, ``narrative``;
* **execution** — ``results`` (per sub-task, merged), ``repairs``, ``repair_log``;
* **verification** — ``findings``, ``consistency``;
* **evidence** — ``llm_calls``, ``visits``, ``errors``;
* **outcome** — ``verdict``, ``status``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from .graph import append_reducer, merge_reducer

KEY_REQUEST = "request"
KEY_OPTIONS = "options"
KEY_INTENT = "intent"
KEY_PLAN = "plan"
KEY_RESULTS = "results"
KEY_REPAIRS = "repairs"
KEY_REPAIR_LOG = "repair_log"
KEY_FINDINGS = "findings"
KEY_CONSISTENCY = "consistency"
KEY_RULING = "ruling"
KEY_NARRATIVE = "narrative"
KEY_VERDICT = "verdict"
KEY_STATUS = "status"
KEY_LLM_CALLS = "llm_calls"
KEY_VISITS = "visits"
KEY_ERRORS = "errors"
KEY_ARTIFACTS = "artifacts"
KEY_PENDING_REPAIRS = "pending_repairs"
#: Written by the graph engine itself at the end of a run (steps, stopped reason, node counts).
KEY_GRAPH = "graph"

#: Collected keys are reduced; every other key is replaced. This is the whole reducer policy.
REDUCERS: Dict[str, Any] = {
    KEY_RESULTS: merge_reducer,
    KEY_FINDINGS: append_reducer,
    KEY_REPAIR_LOG: append_reducer,
    KEY_LLM_CALLS: append_reducer,
    KEY_VISITS: append_reducer,
    KEY_ERRORS: append_reducer,
}

#: Status values. ``ESCALATED`` means "a human must look" — never a silent success.
STATUS_OK = "ok"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_ESCALATED = "escalated"
STATUS_FAILED = "failed"

#: Verdicts. ``escalate`` is distinct from ``review`` on purpose: review is a
#: decision a policy already made (approval required), escalate is the Supervisor
#: admitting its own evidence could not be reconciled.
VERDICT_ALLOW = "allow"
VERDICT_REVIEW = "review"
VERDICT_BLOCK = "block"
VERDICT_ESCALATE = "escalate"


def new_state(
    request: Mapping[str, Any], options: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """Build the initial state for one run: inputs filled, everything else empty."""
    return {
        KEY_REQUEST: dict(request),
        KEY_OPTIONS: dict(options or {}),
        KEY_INTENT: {},
        KEY_PLAN: {},
        KEY_RESULTS: {},
        KEY_REPAIRS: 0,
        KEY_REPAIR_LOG: [],
        KEY_FINDINGS: [],
        KEY_CONSISTENCY: {},
        KEY_RULING: {},
        KEY_NARRATIVE: {},
        KEY_VERDICT: "",
        KEY_STATUS: STATUS_OK,
        KEY_LLM_CALLS: [],
        KEY_VISITS: [],
        KEY_ERRORS: [],
        KEY_ARTIFACTS: {},
        KEY_PENDING_REPAIRS: [],
    }


def subtasks(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Planned sub-tasks, in plan order."""
    plan = state.get(KEY_PLAN) or {}
    tasks = plan.get("subtasks") or []
    return [dict(task) for task in tasks if isinstance(task, Mapping)]


def result_of(state: Mapping[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    return (state.get(KEY_RESULTS) or {}).get(task_id)


def completed_ids(state: Mapping[str, Any]) -> List[str]:
    """Sub-task ids that produced a result — successful or not."""
    return [task_id for task_id, _ in sorted((state.get(KEY_RESULTS) or {}).items())]


def agents_done(state: Mapping[str, Any]) -> List[str]:
    """Agents that produced at least one *successful* result, de-duplicated, sorted."""
    names = []
    for result in (state.get(KEY_RESULTS) or {}).values():
        if isinstance(result, Mapping) and result.get("ok"):
            name = str(result.get("agent") or "")
            if name and name not in names:
                names.append(name)
    return sorted(names)


def agent_names(state: Mapping[str, Any]) -> List[str]:
    """Every agent that produced a result this run, successful or not."""
    names = []
    for result in (state.get(KEY_RESULTS) or {}).values():
        if isinstance(result, Mapping):
            name = str(result.get("agent") or "")
            if name and name not in names:
                names.append(name)
    return sorted(names)


def agent_output(state: Mapping[str, Any], agent: str) -> Optional[Dict[str, Any]]:
    """The most recent successful output from ``agent`` (later attempts win)."""
    found: Optional[Dict[str, Any]] = None
    for task_id, result in (state.get(KEY_RESULTS) or {}).items():
        if not isinstance(result, Mapping):
            continue
        if result.get("agent") == agent and result.get("ok"):
            found = dict(result.get("output") or {})
            found["_subtask"] = task_id
    return found


def outputs_by_agent(state: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """All successful outputs keyed by agent name — the view the Supervisor plans on."""
    collected: Dict[str, Dict[str, Any]] = {}
    for name in agents_done(state):
        output = agent_output(state, name)
        if output is not None:
            collected[name] = output
    return collected


def pending_subtasks(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Planned sub-tasks with no result yet, in plan order."""
    results = state.get(KEY_RESULTS) or {}
    return [task for task in subtasks(state) if str(task.get("id")) not in results]


def ready_subtasks(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Pending sub-tasks whose every ``depends_on`` already has a successful result."""
    results = state.get(KEY_RESULTS) or {}
    ready: List[Dict[str, Any]] = []
    for task in pending_subtasks(state):
        deps = task.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        if all(
            isinstance(dep, str)
            and isinstance(results.get(dep), Mapping)
            and bool((results.get(dep) or {}).get("ok"))
            for dep in deps
        ):
            ready.append(task)
    return ready


def resumable_subtasks(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Sub-tasks that already ran but came back ``incomplete`` and can now be retried.

    A specialist reports *what it was missing* rather than guessing; as soon as those
    inputs exist, re-running it costs nothing and turns "the plan forgot the ordering"
    into a self-healing dispatch instead of a consumed repair round. Sub-tasks that
    failed on their own fields (``missing_fields``) are excluded — nothing will appear
    later to fix those, and retrying them would only spin.
    """
    results = state.get(KEY_RESULTS) or {}
    resumable: List[Dict[str, Any]] = []
    for task in subtasks(state):
        result = results.get(str(task.get("id")))
        if not isinstance(result, Mapping) or not result.get("ok"):
            continue
        output = result.get("output") or {}
        if str(output.get("status", "ok")) == "ok":
            continue
        gaps = [str(gap) for gap in (output.get("missing_inputs") or [])]
        if not gaps or output.get("missing_fields"):
            continue
        if all(agent_output(state, gap) is not None for gap in gaps):
            resumable.append(task)
    return resumable


def dependency_outputs(state: Mapping[str, Any], task: Mapping[str, Any]) -> Dict[str, Any]:
    """The outputs a sub-task declared it depends on, keyed by dependency id."""
    deps = task.get("depends_on") or []
    if isinstance(deps, str):
        deps = [deps]
    results = state.get(KEY_RESULTS) or {}
    collected: Dict[str, Any] = {}
    for dep in deps:
        if isinstance(dep, str):
            result = results.get(dep)
            if isinstance(result, Mapping):
                collected[dep] = dict(result.get("output") or {})
    return collected


def known_citations(state: Mapping[str, Any]) -> Dict[str, str]:
    """Every citation id the run is allowed to reference, mapped to where it comes from.

    Two sources only: the artifact index produced by the evidence agent
    (``artifact:…``) and the policy rule table (``rule:…``). A citation id outside
    this map is unverifiable, which is what consistency rule C3 rejects.
    """
    known: Dict[str, str] = {}
    artifacts = state.get(KEY_ARTIFACTS) or {}
    for ref, meta in artifacts.items():
        if isinstance(meta, Mapping):
            known[str(ref)] = str(meta.get("kind") or "artifact")
    return known


def citation_ids(state: Mapping[str, Any]) -> List[str]:
    """Every ``citations[].id`` any agent emitted, de-duplicated, in first-seen order."""
    ordered: List[str] = []
    for result in (state.get(KEY_RESULTS) or {}).values():
        if not isinstance(result, Mapping):
            continue
        output = result.get("output") or {}
        for citation in output.get("citations") or []:
            cid = str(citation.get("id") or "") if isinstance(citation, Mapping) else str(citation)
            if cid and cid not in ordered:
                ordered.append(cid)
    return ordered


def repair_targets(findings: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Blocking findings translated into the repair work the dispatch node must redo.

    One entry per agent (a later finding can only widen the hint, never drop work),
    so a repair round cannot silently do less than the checker asked for.
    """
    targets: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        if not isinstance(finding, Mapping) or str(finding.get("severity")) != "blocking":
            continue
        repair = finding.get("repair") or {}
        if not isinstance(repair, Mapping):
            continue
        agent = str(repair.get("agent") or "").strip()
        if not agent:
            continue
        goal = str(repair.get("goal") or "")
        if agent in targets:
            targets[agent]["hints"].append(
                "{}: {}".format(finding.get("rule", "?"), finding.get("message", ""))
            )
        else:
            targets[agent] = {
                "agent": agent,
                "goal": goal,
                "hints": ["{}: {}".format(finding.get("rule", "?"), finding.get("message", ""))],
            }
    return list(targets.values())
