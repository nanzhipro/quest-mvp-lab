"""The Supervisor: a lightweight-model agent with exactly four duties.

Its whole job is to *route*, never to answer:

1. **Intent recognition** — map the request onto a closed set of compliance-task
   intents. A composite request comes back with several.
2. **Task planning** — decompose into a DAG of sub-tasks, each naming one specialist
   from the registry and the sub-tasks it depends on. The plan is validated as a
   real DAG before anything runs; a plan the model cannot fix is replaced by the
   deterministic template plan the code already knows.
3. **Routing** — execute the DAG in dependency order, handing each specialist the
   outputs it declared it needs, and re-dispatching named work when the consistency
   checker finds a defect. Routing is deterministic: the model decides the *shape* of
   the work, the code decides the *order* and the retries.
4. **Aggregation** — merge the specialists' outputs into one ruling and write the
   narrative. Grounding is enforced after the fact: a sentence citing an id that does
   not exist is not shown to anyone — the narrative falls back to the deterministic
   template.

Three narrow model calls per request (classify, plan, narrate), each with a strict
JSON contract and a deterministic fallback. Nothing else in the system is
non-deterministic.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import state as st
from .agents.base import AgentContext
from .agents.registry import AgentRegistry
from .graph import validate_dag
from .jsonio import parse_object, string_list
from .llm import ChatModel, LLMError
from .predicates import level_rank, strictest_decision

# ── duty 1: the closed intent set ─────────────────────────────────────────────
INTENTS: Dict[str, str] = {
    "data_classification": "资产敏感级别判定（这份东西是什么级别）",
    "policy_applicability": "策略适用性判定（哪些管控规则适用、结论是什么）",
    "evidence_verification": "证据核验（提交的材料是否齐备、可复算）",
    "remediation_planning": "处置建议（应当采取哪些动作、谁负责、时限）",
    "compliance_ruling": "合规裁决（综合上述结论给出最终结论）",
}
DEFAULT_INTENT = "compliance_ruling"

KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("data_classification", ("级别", "分级", "敏感", "定级")),
    ("policy_applicability", ("策略", "规则", "制度", "适用", "阈值")),
    ("evidence_verification", ("证据", "佐证", "材料", "核验", "审批单")),
    ("remediation_planning", ("处置", "建议", "整改", "动作", "阻断")),
]

CLASSIFY_SYSTEM = """你是合规编排层的路由智能体，只负责意图识别这一件事。
给定一条合规请求，判断它包含下列哪些任务意图（可多选），并给出主意图。
可选意图（必须使用这些英文标识，不得自造）：
{intent_lines}

只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码块，格式：
{{"intents": ["<意图标识>", ...], "primary": "<意图标识>",
  "reason": "<不超过 40 字的判断依据>"}}
约束：intents 非空且去重；primary 必须出现在 intents 中；无法判断时用 {default}。"""

PLAN_SYSTEM = """你是合规编排层的路由智能体，只负责任务规划这一件事：
把请求拆成有向无环的子任务，派给可用的专业 Agent。

可用专业 Agent（不得使用未列出的名字）：
{catalogue}

只输出一个 JSON 对象，不要解释文字、不要代码块，格式：
{{"subtasks": [{{"id": "t1", "agent": "<agent 名>", "goal": "<该子任务要交付什么>",
  "depends_on": ["<前置子任务 id>"]}}], "rationale": "<不超过 60 字的编排理由>"}}
硬性规则：
1. id 唯一且简短（t1、t2…）；agent 只能取上面列出的名字；
2. depends_on 只能引用本计划内已出现的 id，不允许环；
3. 某 Agent 声明的"需要"必须在它之前被规划出来，否则该 Agent 只能给出残次结论；
4. 不要重复规划同一 Agent；确有必要的复合任务，用最少子任务覆盖意图。"""

AGGREGATE_SYSTEM = """你是合规编排层的路由智能体，只负责结果聚合中的"叙述"这一件事。
给定各专业 Agent 的确定性结论摘要与可引用的证据 id，写 3~5 句中文结论说明。
硬性规则：
1. 只能引用给出的证据 id，格式如 [rule:R-03]、[artifact:file.csv]，不得编造；
2. 不得引入摘要里没有的数字、级别、规则或动作；
3. 不要复述全部细节，只给可执行的结论；
4. 直接输出正文，不要标题、不要 Markdown 代码块、不要 JSON。"""


class Supervisor:
    """The routing agent. Holds a model client and the registry it is allowed to route to."""

    def __init__(
        self,
        model: ChatModel,
        registry: AgentRegistry,
        *,
        max_repairs: int = 1,
        max_dispatch: int = 24,
    ) -> None:
        self.model = model
        self.registry = registry
        self.max_repairs = max(0, int(max_repairs))
        self.max_dispatch = max(1, int(max_dispatch))
        self.data_dir = registry.data_dir
        self.calls: List[Dict[str, Any]] = []

    # ── duty 1: intent recognition ────────────────────────────────────────────
    def classify(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the request's intents. Falls back to keyword rules on any bad reply."""
        prompt = CLASSIFY_SYSTEM.format(
            intent_lines="\n".join("- {}：{}".format(name, text) for name, text in INTENTS.items()),
            default=DEFAULT_INTENT,
        )
        request_text = self._request_brief(request)
        reply, meta = self._call("classify", prompt, request_text)
        if reply is not None:
            intents = string_list(reply, "intents", allow_empty=True)
            primary = str(reply.get("primary") or "").strip()
            chosen = [name for name in intents.values if name in INTENTS]
            if primary in INTENTS and primary not in chosen:
                chosen.append(primary)
            if chosen:
                primary = primary if primary in chosen else chosen[0]
                return {
                    "primary": primary,
                    "intents": chosen,
                    "composite": len(chosen) > 1,
                    "source": "llm",
                    "reason": str(reply.get("reason") or ""),
                    "llm": meta,
                }
            meta["parse_error"] = "reply listed no known intent"
        fallback = self._keyword_intents(str(request.get("text") or "") or request_text)
        return {
            "primary": fallback[0] if fallback else DEFAULT_INTENT,
            "intents": fallback or [DEFAULT_INTENT],
            "composite": len(fallback) > 1,
            "source": "fallback",
            "reason": "模型回复不可用时按关键词兜底",
            "llm": meta,
        }

    def _keyword_intents(self, text: str) -> List[str]:
        hits = [name for name, words in KEYWORDS if any(word in text for word in words)]
        if "compliance_ruling" in text or "裁决" in text or "结论" in text:
            hits.append("compliance_ruling")
        if not hits:
            return [DEFAULT_INTENT]
        return hits

    # ── duty 2: task planning ─────────────────────────────────────────────────
    def plan(self, request: Mapping[str, Any], intent: Mapping[str, Any]) -> Dict[str, Any]:
        """Produce a validated sub-task DAG, repairing or replacing the model's plan."""
        prompt = PLAN_SYSTEM.format(catalogue=self._catalogue())
        request_text = "{}\n\n意图识别结果：{}（primary={}）".format(
            self._request_brief(request),
            ", ".join(intent.get("intents") or []),
            intent.get("primary"),
        )
        reply, meta = self._call("plan", prompt, request_text)
        problems: List[str] = []
        if reply is not None:
            candidate = self._normalise_subtasks(reply.get("subtasks") or [])
            problems = validate_dag(candidate, self.registry.names)
            if not problems and candidate:
                return {
                    "subtasks": candidate,
                    "rationale": str(reply.get("rationale") or ""),
                    "source": "llm",
                    "validation": {"problems": [], "repaired": False},
                    "llm": meta,
                }
            if not candidate:
                problems = ["模型没有给出任何子任务"]
        # One repair round: hand the model its own error.
        repair_note = "\n".join("- {}".format(problem) for problem in problems)
        repaired, repair_meta = self._call(
            "plan",
            prompt,
            "{}\n\n上一次计划不合法，必须修正以下问题后重新输出完整 JSON：\n{}".format(
                request_text, repair_note
            ),
        )
        if repaired is not None:
            candidate = self._normalise_subtasks(repaired.get("subtasks") or [])
            remaining = validate_dag(candidate, self.registry.names)
            if candidate and not remaining:
                return {
                    "subtasks": candidate,
                    "rationale": str(repaired.get("rationale") or ""),
                    "source": "llm_repaired",
                    "validation": {"problems": problems, "repaired": True},
                    "llm": repair_meta,
                }
            problems = problems + remaining
        fallback = template_plan(intent.get("intents") or [intent.get("primary") or DEFAULT_INTENT])
        return {
            "subtasks": fallback,
            "rationale": "模型计划不可用，回退到代码内的确定性模板计划",
            "source": "fallback",
            "validation": {"problems": problems, "repaired": False},
            "llm": meta,
        }

    def _normalise_subtasks(self, raw: Sequence[Any]) -> List[Dict[str, Any]]:
        """Coerce the model's sub-task array into the plan's canonical shape."""
        tasks: List[Dict[str, Any]] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, Mapping):
                continue
            deps = item.get("depends_on")
            if isinstance(deps, str):
                deps = [deps]
            if not isinstance(deps, list):
                deps = []
            tasks.append(
                {
                    "id": str(item.get("id") or "t{}".format(index)).strip(),
                    "agent": str(item.get("agent") or "").strip(),
                    "goal": str(item.get("goal") or "").strip(),
                    "depends_on": [str(dep).strip() for dep in deps if str(dep).strip()],
                }
            )
        return tasks

    # ── duty 3: routing ───────────────────────────────────────────────────────
    def route(
        self,
        run_state: Mapping[str, Any],
        *,
        repairs: Optional[Sequence[Mapping[str, Any]]] = None,
        round_index: int = 0,
    ) -> Dict[str, Any]:
        """Execute planned work in dependency order; returns a graph state update.

        Two things get dispatched in one call:

        * the **ready set** — pending sub-tasks whose declared dependencies ran (or, in
          a repair round, the agents the consistency checker named, synthesising a
          sub-task for an agent the plan never scheduled);
        * the **resumable set** — sub-tasks that ran before their inputs existed and
          can now be retried, which is how a planning slip about *ordering* is repaired
          without spending the repair budget.

        Each sub-task id executes at most once per call, so a mutual dependency between
        two `incomplete` outputs cannot spin: it is the graph's check node, not this
        loop, that decides whether the run deserves another round.
        """
        results: Dict[str, Any] = {}
        artifacts: Dict[str, Any] = {}
        log: List[Dict[str, Any]] = []
        executed = 0
        executed_ids: set = set()
        working: Dict[str, Any] = dict(run_state)
        queue: List[Dict[str, Any]] = []
        if repairs:
            for repair in repairs:
                agent = str(repair.get("agent") or "")
                task = self._task_for_agent(working, agent)
                synthesized = False
                if task is None:
                    task = {
                        "id": "r{}-{}".format(round_index, agent or "unknown"),
                        "agent": agent,
                        "goal": str(repair.get("goal") or "补齐缺失的结论"),
                        "depends_on": [],
                    }
                    synthesized = True
                hint = "\n".join(str(item) for item in repair.get("hints") or [])
                queue.append(
                    {"task": task, "hint": hint, "synthesized": synthesized, "repair": True}
                )
                log.append(
                    {
                        "round": round_index,
                        "agent": agent,
                        "subtask": task["id"],
                        "synthesized": synthesized,
                        "hint": hint,
                        "attempt": self._next_attempt(working, str(task["id"])),
                    }
                )
        for task in self._follow_up(working):
            queue.append({"task": task, "hint": "", "synthesized": False, "repair": False})

        while queue and executed < self.max_dispatch:
            item = queue.pop(0)
            task = item["task"]
            task_id = str(task.get("id"))
            if task_id in executed_ids:
                continue
            executed_ids.add(task_id)
            executed += 1
            record = self._execute(
                working,
                task,
                hint=item["hint"],
                attempt=self._next_attempt(working, task_id),
            )
            results[task_id] = record
            output = record.get("output") or {}
            if isinstance(output.get("index"), Mapping):
                artifacts.update({str(key): dict(value) for key, value in output["index"].items()})
            working[st.KEY_RESULTS] = {
                **dict(working.get(st.KEY_RESULTS) or {}),
                task_id: record,
            }
            for pending in self._follow_up(working):
                if str(pending.get("id")) in executed_ids:
                    continue
                if str(pending.get("id")) in [str(entry["task"].get("id")) for entry in queue]:
                    continue
                queue.append({"task": pending, "hint": "", "synthesized": False, "repair": False})
        update: Dict[str, Any] = {"results": results}
        if artifacts:
            update["artifacts"] = artifacts
        if log:
            update["repair_log"] = log
        return update

    def _follow_up(self, run_state: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """What may run next: pending sub-tasks that are ready, plus retryable ones."""
        ordered: List[Dict[str, Any]] = []
        for task in st.ready_subtasks(run_state) + st.resumable_subtasks(run_state):
            if str(task.get("id")) not in [str(seen.get("id")) for seen in ordered]:
                ordered.append(task)
        return ordered

    def _execute(
        self,
        run_state: Mapping[str, Any],
        task: Mapping[str, Any],
        *,
        hint: str,
        attempt: int,
    ) -> Dict[str, Any]:
        """Run one sub-task through its specialist and stamp the result envelope."""
        agent_name = str(task.get("agent") or "")
        agent = self.registry.get(agent_name)
        started = time.perf_counter()
        if agent is None:
            return {
                "subtask": task.get("id"),
                "agent": agent_name,
                "goal": task.get("goal"),
                "ok": False,
                "error": "unknown agent {!r} (plan validation should have caught this)".format(
                    agent_name
                ),
                "output": {},
                "attempt": attempt,
                "hint": hint,
                "duration_ms": 0,
            }
        dep_ids = {
            str(dep): str((self._task_by_id(run_state, str(dep)) or {}).get("agent") or "")
            for dep in task.get("depends_on") or []
        }
        deps_by_agent: Dict[str, Dict[str, Any]] = {}
        # Declared dependencies control *order*; a specialist's `requires` controls
        # *visibility*. Supplying both is what lets a repair round succeed when the
        # plan forgot to declare the dependency it actually needed.
        for dep_id, dep_agent in dep_ids.items():
            if not dep_agent:
                continue
            result = st.result_of(run_state, dep_id)
            if isinstance(result, Mapping):
                deps_by_agent[dep_agent] = dict(result.get("output") or {})
        for required in agent.requires:
            if required in deps_by_agent:
                continue
            available = st.agent_output(run_state, required)
            if available is not None:
                deps_by_agent[required] = available
        ctx = AgentContext(
            request=run_state.get(st.KEY_REQUEST) or {},
            goal=str(task.get("goal") or ""),
            data_dir=self.data_dir,
            deps=deps_by_agent,
            dep_ids=dep_ids,
            hint=hint,
            attempt=attempt,
        )
        error = ""
        try:
            output = agent.invoke(ctx)
        except Exception as exc:
            output = {"status": "failed", "citations": [], "missing_inputs": [], "reason": str(exc)}
            error = "{}: {}".format(type(exc).__name__, exc)
        return {
            "subtask": task.get("id"),
            "agent": agent_name,
            "goal": task.get("goal"),
            "ok": not error,
            "error": error,
            "output": output,
            "attempt": attempt,
            "hint": hint,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    def _task_by_id(self, run_state: Mapping[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
        for task in st.subtasks(run_state):
            if str(task.get("id")) == task_id:
                return task
        return None

    def _task_for_agent(self, run_state: Mapping[str, Any], agent: str) -> Optional[Dict[str, Any]]:
        for task in st.subtasks(run_state):
            if str(task.get("agent")) == agent:
                return task
        return None

    def _next_attempt(self, run_state: Mapping[str, Any], task_id: str) -> int:
        existing = st.result_of(run_state, task_id)
        if isinstance(existing, Mapping):
            return int(existing.get("attempt") or 1) + 1
        return 1

    # ── duty 4: aggregation + consistency-checked narrative ───────────────────
    def aggregate(
        self,
        run_state: Mapping[str, Any],
        consistency: Optional[Mapping[str, Any]] = None,
        *,
        narrate: bool = True,
    ) -> Dict[str, Any]:
        """Merge specialist outputs into one ruling and a grounded narrative.

        ``consistency`` defaults to the snapshot the check node already put in state,
        and ``narrate=False`` skips the model entirely (the escalation path uses it so
        a run that needs a human does not spend a call on prose).
        """
        consistency = dict(consistency or run_state.get(st.KEY_CONSISTENCY) or {})
        classification = st.agent_output(run_state, "classification") or {}
        policy = st.agent_output(run_state, "policy") or {}
        evidence = st.agent_output(run_state, "evidence") or {}
        remediation = st.agent_output(run_state, "remediation") or {}
        matched = [dict(rule) for rule in policy.get("matched_rules") or []]
        decision = str(policy.get("decision") or remediation.get("decision_used") or "")
        level = str(classification.get("level") or "")
        verdict = "escalate" if (consistency.get("blocking") or 0) > 0 else (decision or "review")
        ruling: Dict[str, Any] = {
            "verdict": verdict,
            "level": level,
            "decision": decision,
            "matched_rules": matched,
            "actions": [dict(action) for action in remediation.get("actions") or []],
            "evidence": {
                "verified": list(evidence.get("verified") or []),
                "unverified": list(evidence.get("unverified") or []),
            },
            "detectors": list(classification.get("detectors") or []),
            "findings": list(consistency.get("findings") or []),
            "consistency": {
                "passed": bool(consistency.get("passed")),
                "blocking": int(consistency.get("blocking") or 0),
                "warnings": int(consistency.get("warnings") or 0),
            },
            "agents": st.agents_done(run_state),
        }
        narrative = (
            self._narrate(run_state, ruling)
            if narrate
            else {
                "text": deterministic_narrative(ruling),
                "source": "deterministic_template",
                "unknown_citations": [],
                "llm": {},
            }
        )
        ruling["narrative"] = narrative["text"]
        ruling["narrative_source"] = narrative["source"]
        return {
            "ruling": ruling,
            "narrative": narrative,
            "verdict": verdict,
            "llm_calls": [narrative["llm"]] if narrative.get("llm") else [],
        }

    def _narrate(self, run_state: Mapping[str, Any], ruling: Mapping[str, Any]) -> Dict[str, Any]:
        """Ask the model for the summary, then keep it only if every citation resolves."""
        allowed = sorted(st.known_citations(run_state))
        digest = {
            "级别": ruling.get("level"),
            "决策": ruling.get("decision"),
            "裁决": ruling.get("verdict"),
            "命中规则": [
                "{} {}".format(rule.get("id"), rule.get("name"))
                for rule in ruling.get("matched_rules") or []
            ],
            "处置动作": [
                "{}（{}，{}h）".format(a.get("name"), a.get("owner"), a.get("sla_hours"))
                for a in ruling.get("actions") or []
            ],
            "证据": [
                "{}: 已核验".format(item.get("id"))
                for item in (ruling.get("evidence") or {}).get("verified") or []
            ]
            + [
                "{}: 未核验（{}）".format(item.get("id"), item.get("reason"))
                for item in (ruling.get("evidence") or {}).get("unverified") or []
            ],
            "一致性问题": [
                "[{}] {}".format(f.get("rule"), f.get("message"))
                for f in ruling.get("findings") or []
            ],
            "可引用 id": allowed,
        }
        payload = "\n".join("- {}: {}".format(key, value) for key, value in digest.items())
        reply, meta = self._call("narrative", AGGREGATE_SYSTEM, payload, expect_json=False)
        text = str((reply or {}).get("text") or "").strip()
        if text:
            unknown = unknown_citations(text, allowed)
            if not unknown:
                return {"text": text, "source": "llm", "unknown_citations": [], "llm": meta}
            meta["grounding_error"] = "unknown citations: {}".format(", ".join(unknown))
            return {
                "text": deterministic_narrative(ruling),
                "source": "deterministic_template",
                "unknown_citations": unknown,
                "llm": meta,
            }
        return {
            "text": deterministic_narrative(ruling),
            "source": "deterministic_template",
            "unknown_citations": [],
            "llm": meta,
        }

    # ── model plumbing ────────────────────────────────────────────────────────
    def _catalogue(self) -> str:
        lines = []
        for row in self.registry.describe():
            lines.append(
                "- {name}：{mission}｜需要：{needs}｜产出字段：{keys}".format(
                    name=row["name"],
                    mission=row["mission"],
                    needs=", ".join(row["requires"]) or "无",
                    keys=", ".join(row["output_keys"]),
                )
            )
        return "\n".join(lines)

    def _request_brief(self, request: Mapping[str, Any]) -> str:
        asset = request.get("asset") or {}
        evidence = request.get("evidence") or []
        lines = [
            "请求：{}".format(request.get("text") or ""),
            "资产：{}（类型 {}，通道 {}，去向 {}，数据属主 {}）".format(
                asset.get("path") or "未提供",
                asset.get("kind") or "未知",
                asset.get("channel") or "未知",
                asset.get("destination") or "未知",
                asset.get("owner") or "未知",
            ),
            "附带证据：{}".format(
                ", ".join(str(item.get("id")) for item in evidence if isinstance(item, Mapping))
                or "无"
            ),
        ]
        if request.get("notes"):
            lines.append("备注：{}".format(request.get("notes")))
        return "\n".join(lines)

    def _call(
        self,
        purpose: str,
        system_prompt: str,
        user_prompt: str,
        *,
        expect_json: bool = True,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """One narrow model call. Returns ``(parsed_json_or_None, call_digest)``.

        A failed call is never fatal: the digest records why, and the caller has a
        deterministic path. That is what keeps the Supervisor optional rather than
        load-bearing for correctness.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        digest: Dict[str, Any] = {
            "purpose": purpose,
            "prompt_chars": len(system_prompt) + len(user_prompt),
            "reply_chars": 0,
            "parsed": False,
            "error": "",
        }
        try:
            completion = self.model.complete(messages)
        except LLMError as exc:
            digest["error"] = str(exc)
            self.calls.append(digest)
            return None, digest
        digest["reply_chars"] = len(completion.text)
        digest["latency_ms"] = completion.latency_ms
        digest["usage"] = dict(completion.usage)
        digest["model"] = getattr(getattr(self.model, "config", None), "model", "")
        if not expect_json:
            text = completion.text.strip()
            self.calls.append(digest)
            return ({"text": text} if text else None), digest
        parsed = parse_object(completion.text)
        if not parsed.ok:
            digest["error"] = parsed.error
            self.calls.append(digest)
            return None, digest
        digest["parsed"] = True
        self.calls.append(digest)
        return parsed.value, digest


# ── deterministic fallbacks ───────────────────────────────────────────────────
#: Intent → the specialist that owns it (the template plan's translation table).
INTENT_TO_AGENT = {
    "data_classification": "classification",
    "policy_applicability": "policy",
    "evidence_verification": "evidence",
    "remediation_planning": "remediation",
}

#: The dependency knowledge the code owns — kept in sync with the agent contracts by
#: ``tests/test_supervisor.py::test_template_dependencies_match_the_agent_contracts``.
AGENT_REQUIRES = {
    "classification": (),
    "evidence": (),
    "policy": ("classification",),
    "remediation": ("classification", "policy"),
}

CANONICAL_PLAN = (
    {"id": "t1", "agent": "classification", "goal": "判定资产敏感级别", "depends_on": []},
    {"id": "t2", "agent": "evidence", "goal": "核验请求附带的证据", "depends_on": []},
    {"id": "t3", "agent": "policy", "goal": "判定适用策略与决策", "depends_on": ["t1"]},
    {"id": "t4", "agent": "remediation", "goal": "生成处置动作建议", "depends_on": ["t1", "t3"]},
)


def template_plan(intents: Sequence[str]) -> List[Dict[str, Any]]:
    """The dependency graph the code already knows, keyed by the intent set.

    This is what makes the Supervisor's planning *optional*: if the model is down, slow,
    or wrong, the contracts between agents still produce a valid DAG. Intents are
    translated to agents here and closed transitively over ``AGENT_REQUIRES`` — asking
    for remediation implies policy implies classification, whether or not the intent
    list mentioned them.
    """
    needs: set = set()
    for intent in intents or ():
        if intent == "compliance_ruling":
            needs.update(AGENT_REQUIRES)
        elif intent in INTENT_TO_AGENT:
            needs.add(INTENT_TO_AGENT[intent])
    if not needs:
        needs.update(AGENT_REQUIRES)
    for _ in range(len(AGENT_REQUIRES)):  # transitive closure, order-independent
        for agent, requires in AGENT_REQUIRES.items():
            if agent in needs:
                needs.update(requires)
    tasks = [dict(task) for task in CANONICAL_PLAN if task["agent"] in needs]
    kept = {task["id"] for task in tasks}
    for task in tasks:
        task["depends_on"] = [dep for dep in task["depends_on"] if dep in kept]
    return tasks


_CITATION_RE = re.compile(r"\[(rule:[A-Za-z0-9._-]+|artifact:[^\]\s]+)\]")


def citations_in(text: str) -> List[str]:
    """Every ``[rule:…]`` / ``[artifact:…]`` citation appearing in a piece of prose."""
    seen: List[str] = []
    for match in _CITATION_RE.finditer(text or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def unknown_citations(text: str, allowed: Sequence[str]) -> List[str]:
    """Citations in ``text`` that do not resolve — the grounding test for the narrative."""
    permitted = set(allowed)
    return [cid for cid in citations_in(text) if cid not in permitted]


def deterministic_narrative(ruling: Mapping[str, Any]) -> str:
    """The template narrative: every sentence is assembled from checked fields only."""
    level = ruling.get("level") or "未定级"
    verdict = ruling.get("verdict") or "review"
    rules = ruling.get("matched_rules") or []
    actions = ruling.get("actions") or []
    verified = (ruling.get("evidence") or {}).get("verified") or []
    unverified = (ruling.get("evidence") or {}).get("unverified") or []
    parts = [
        "资产级别判定为 {}。".format(level),
        "适用规则 {}，裁决为 {}。".format(
            "、".join(str(rule.get("id")) for rule in rules) or "无", verdict
        ),
        "处置动作 {}。".format(
            "、".join(
                "{}({})".format(action.get("name"), action.get("owner")) for action in actions
            )
            or "无"
        ),
    ]
    if verified:
        parts.append("已核验证据 {}。".format("、".join(str(item.get("id")) for item in verified)))
    if unverified:
        parts.append(
            "未核验证据 {}，需人工补齐。".format(
                "、".join(str(item.get("id")) for item in unverified)
            )
        )
    return "".join(parts)


def level_of(ruling: Mapping[str, Any]) -> str:
    """Convenience for callers that need the ruling's level rank."""
    return str(ruling.get("level") or "")


__all__ = [
    "AGGREGATE_SYSTEM",
    "CLASSIFY_SYSTEM",
    "DEFAULT_INTENT",
    "INTENTS",
    "KEYWORDS",
    "PLAN_SYSTEM",
    "Supervisor",
    "citations_in",
    "deterministic_narrative",
    "level_rank",
    "strictest_decision",
    "template_plan",
    "unknown_citations",
]
