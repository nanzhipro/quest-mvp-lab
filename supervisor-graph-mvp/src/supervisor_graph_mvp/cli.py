"""Command-line surface: run, plan, graph, report, probe.

Two conventions borrowed from the sibling MVPs because they make a demo debuggable:

* **stdout carries the result** (the ruling, or ``--json`` for the whole payload);
  everything procedural — trace lines, ``wrote <path>``, errors — goes to stderr.
* **Exit codes distinguish "ran" from "needs a human"**: ``0`` a ruling was produced,
  ``1`` the run itself failed (transport, malformed model output beyond the
  fallbacks, truncated graph), ``2`` configuration/usage error, ``3`` the run
  escalated to human review. ``3`` is not a bug — it is the redesign's honest
  outcome for evidence that cannot be reconciled.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .agents.base import AgentError
from .agents.registry import default_registry
from .config import Config, ConfigError
from .demo import load_script, scripted_scenarios
from .llm import ChatModel, DeepSeekClient, LLMError, ScriptedClient
from .pipeline import Orchestrator, build_orchestrator
from .reporting import render_ruling, result_payload, summarize
from .scenarios import find_scenario, load_request, load_scenarios
from .supervisor import Supervisor
from .trace import Tracer, make_run_id, trace_dir
from .viz import report_from_run_dir, write_report

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CONFIG = 2
EXIT_ESCALATED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supervisor-graph",
        description=(
            "Supervisor + 状态图编排层 MVP：意图识别 → 任务规划(DAG) → 路由派发 → "
            "结果聚合（含一致性检查）"
        ),
    )
    parser.add_argument(
        "--version", action="version", version="supervisor-graph-mvp {}".format(__version__)
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="端到端跑一条合规请求")
    run.add_argument("scenario", nargs="?", help="内置场景 id（可用 scenarios 子命令列出）")
    run.add_argument("--request", help="自定义请求 JSON 文件")
    run.add_argument("--all", action="store_true", help="依次跑完全部内置场景")
    run.add_argument("--fake", action="store_true", help="用内置脚本化模型离线重放（不需要 key）")
    run.add_argument("--script-file", help="自定义脚本化模型 JSON 文件")
    run.add_argument("--model", help="覆盖模型名（默认取 DEEPSEEK_MODEL 或 deepseek-chat）")
    run.add_argument("--base-url", help="覆盖 OpenAI 兼容端点")
    run.add_argument("--api-key", help="覆盖 DEEPSEEK_API_KEY")
    run.add_argument("--max-supersteps", type=int, help="状态图步数预算（默认 24）")
    run.add_argument("--max-repairs", type=int, help="修复轮次预算（默认 1）")
    run.add_argument("--trace-dir", help="落盘目录：trace.jsonl / wire.jsonl / result.json")
    run.add_argument("--html", help="额外生成单文件可下钻 HTML 报告")
    run.add_argument(
        "--dump-prompts", action="store_true", help="把每次模型调用的提示词打到 stderr"
    )
    run.add_argument("--json", action="store_true", help="stdout 只输出结构化结果")
    run.add_argument("--quiet", action="store_true", help="不输出过程 trace")

    plan = sub.add_parser("plan", help="只做意图识别与任务规划（不派发执行）")
    plan.add_argument("scenario", nargs="?", help="内置场景 id")
    plan.add_argument("--request", help="自定义请求 JSON 文件")
    plan.add_argument("--fake", action="store_true", help="用内置脚本化模型")
    plan.add_argument("--script-file", help="自定义脚本化模型 JSON 文件")
    plan.add_argument("--model")
    plan.add_argument("--base-url")
    plan.add_argument("--api-key")
    plan.add_argument("--json", action="store_true")

    graph = sub.add_parser("graph", help="打印状态图结构")
    graph.add_argument("--format", choices=("table", "mermaid"), default="table")

    report = sub.add_parser("report", help="从一次运行的目录重建 HTML 报告")
    report.add_argument("run_dir", help="包含 result.json / trace.jsonl / wire.jsonl 的目录")
    report.add_argument("--out", help="输出 HTML 路径（默认 <run_dir>/report.html）")

    sub.add_parser("scenarios", help="列出内置场景")
    sub.add_parser("probe", help="连通性自检：模型清单 + 一次 ping")

    return parser


# ── shared plumbing ───────────────────────────────────────────────────────────
def _make_model(args: argparse.Namespace, scenario_id: Optional[str]) -> ChatModel:
    """Pick the model client: scripted when ``--fake``/``--script-file``, else the real one."""
    if getattr(args, "fake", False) or getattr(args, "script_file", None):
        if getattr(args, "script_file", None):
            document = json.loads(Path(args.script_file).read_text(encoding="utf-8"))
            script = document.get("script") if isinstance(document, dict) else document
            if not isinstance(script, list):
                raise ConfigError(
                    "script file must contain a JSON list or a {'script': [...]} object"
                )
            return ScriptedClient(script, label=Path(args.script_file).stem)
        if not scenario_id:
            raise ConfigError("--fake needs a scenario id (or --all) so a script can be selected")
        return ScriptedClient(load_script(scenario_id), label=scenario_id)
    config = Config.from_env(
        api_key=getattr(args, "api_key", None),
        base_url=getattr(args, "base_url", None),
        model=getattr(args, "model", None),
        max_supersteps=getattr(args, "max_supersteps", None),
        max_repairs=getattr(args, "max_repairs", None),
    )
    return DeepSeekClient(config)


def _resolve_request(args: argparse.Namespace) -> Dict[str, Any]:
    if getattr(args, "request", None):
        return load_request(Path(args.request))
    name = getattr(args, "scenario", None)
    if not name:
        raise ConfigError("give a scenario id, --request FILE, or --all")
    return find_scenario(name)


def _make_tracer(args: argparse.Namespace, label: str, run_id: Optional[str] = None) -> Tracer:
    run_id = run_id or make_run_id(label)
    base = getattr(args, "trace_dir", None)
    directory = trace_dir(Path(base), run_id) if base else None
    return Tracer(
        run_id=run_id,
        trace_path=(directory / "trace.jsonl") if directory else None,
        wire_path=(directory / "wire.jsonl") if directory else None,
        result_path=(directory / "result.json") if directory else None,
        console=not getattr(args, "quiet", False),
    )


def _dump_prompts(model: ChatModel, tracer: Tracer) -> None:
    """Print every request/response pair. Explicitly requested, so it ignores ``--quiet``.

    The real client carries the request body in its exchange records; the scripted client
    has no wire body, so its own recorded calls are used instead.
    """
    exchanges = list(model.exchanges or [])
    calls = list(getattr(model, "calls", []) or [])
    for index in range(max(len(exchanges), len(calls))):
        exchange = exchanges[index] if index < len(exchanges) else {}
        body = (exchange.get("request") or {}).get("body") or {}
        messages = (
            body.get("messages")
            or (calls[index].get("messages") if index < len(calls) else None)
            or []
        )
        for message in messages:
            print(
                "[prompt #{}.{}] {}".format(
                    index, message.get("role"), (message.get("content") or "")[:2000]
                ),
                file=sys.stderr,
            )
        print(
            "[reply #{}.0] {}".format(index, (exchange.get("response_raw") or "")[:2000]),
            file=sys.stderr,
        )


# ── commands ──────────────────────────────────────────────────────────────────
def cmd_run(args: argparse.Namespace) -> int:
    if args.all:
        return _run_all(args)
    request = _resolve_request(args)
    scenario_id = str(request.get("id") or "ad-hoc")
    model = _make_model(args, scenario_id)
    tracer = _make_tracer(args, scenario_id)
    orchestrator = build_orchestrator(
        model,
        max_repairs=args.max_repairs if args.max_repairs is not None else 1,
        max_supersteps=args.max_supersteps if args.max_supersteps is not None else 24,
        tracer=tracer,
    )
    run_state = orchestrator.run(request, options={"mode": "fake" if args.fake else "live"})
    payload = result_payload(run_state)
    tracer.write_wire(list(model.exchanges))
    tracer.write_result(payload)
    tracer.say(
        "wrote {}".format(tracer.result_path)
        if tracer.result_path
        else "（未落盘，可用 --trace-dir 保留证据）"
    )
    if args.dump_prompts:
        _dump_prompts(model, tracer)
    html_path = None
    if args.html:
        html_path = write_report(Path(args.html), payload, tracer.events, list(model.exchanges))
        tracer.say("wrote {}".format(html_path))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_ruling(payload.get("ruling") or {}))
        print("> {}".format(summarize(run_state)))
    stopped = str((run_state.get("graph") or {}).get("stopped_reason") or "")
    if stopped not in ("end",):
        tracer.say("! 图未正常收口：{}".format(stopped))
        return EXIT_RUN_FAILED
    verdict = str(run_state.get("verdict") or "")
    if verdict == "escalate":
        return EXIT_ESCALATED
    return EXIT_OK


def _run_all(args: argparse.Namespace) -> int:
    """Run every packaged scenario. Each scenario gets its own run directory and tracer."""
    worst = EXIT_OK
    rows: List[Dict[str, Any]] = []
    for scenario in load_scenarios():
        scenario_id = str(scenario.get("id"))
        scenario_args = argparse.Namespace(**vars(args))
        scenario_args.all = False
        scenario_args.scenario = scenario_id
        scenario_args.json = False
        print("══ {} ─ {}".format(scenario_id, scenario.get("title")), file=sys.stdout)
        code = cmd_run(scenario_args)
        worst = max(worst, code if code != EXIT_ESCALATED else EXIT_OK)
        rows.append({"scenario": scenario_id, "exit_code": code})
    if args.json:
        print(json.dumps({"runs": rows}, ensure_ascii=False, indent=2))
    return worst if worst != EXIT_ESCALATED else EXIT_OK


def cmd_plan(args: argparse.Namespace) -> int:
    request = _resolve_request(args)
    scenario_id = str(request.get("id") or "ad-hoc")
    model = _make_model(args, scenario_id)
    registry = default_registry()
    supervisor = Supervisor(model, registry)
    intent = supervisor.classify(request)
    plan = supervisor.plan(request, intent)
    payload = {"request": request, "intent": intent, "plan": plan}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            "意图：{}（primary={}，来源 {}，composite={}）".format(
                ", ".join(intent.get("intents") or []),
                intent.get("primary"),
                intent.get("source"),
                intent.get("composite"),
            )
        )
        print("理由：{}".format(intent.get("reason") or "-"))
        print("计划来源：{}".format(plan.get("source")))
        print(
            "校验问题：{}".format(
                "；".join(plan.get("validation", {}).get("problems") or []) or "无"
            )
        )
        print("")
        print("| 子任务 | Agent | 目标 | 依赖 |")
        print("| --- | --- | --- | --- |")
        for task in plan.get("subtasks") or []:
            print(
                "| {} | {} | {} | {} |".format(
                    task.get("id"),
                    task.get("agent"),
                    task.get("goal"),
                    ", ".join(task.get("depends_on") or []) or "-",
                )
            )
    return EXIT_OK


def cmd_graph(args: argparse.Namespace) -> int:
    orchestrator = Orchestrator(ScriptedClient([], label="offline"))
    if args.format == "mermaid":
        print(orchestrator.mermaid())
        return EXIT_OK
    print("| 节点 | 职责 | 后继 | 条件边 |")
    print("| --- | --- | --- | --- |")
    for row in orchestrator.describe():
        print(
            "| {} | {} | {} | {} |".format(
                row["node"],
                row["description"],
                ", ".join(row["successors"]) or "END",
                "是" if row["conditional"] else "否",
            )
        )
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    path = report_from_run_dir(Path(args.run_dir), out=Path(args.out) if args.out else None)
    print(str(path))
    return EXIT_OK


def cmd_scenarios() -> int:
    for scenario in load_scenarios():
        print("{}  {}".format(scenario.get("id"), scenario.get("title")))
        print("    {}".format(scenario.get("text")))
    print("")
    print("离线可重放：{}".format(", ".join(scripted_scenarios())))
    return EXIT_OK


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        config = Config.from_env(
            api_key=getattr(args, "api_key", None),
            base_url=getattr(args, "base_url", None),
            model=getattr(args, "model", None),
        )
    except ConfigError as exc:
        print("配置错误：{}".format(exc), file=sys.stderr)
        return EXIT_CONFIG
    client = DeepSeekClient(config)
    print("端点：{}".format(config.chat_completions_url))
    print("模型：{}".format(config.model))
    print("密钥：{}".format(config.masked_key()))
    try:
        models = client.list_models()
        print(
            "可用模型（{} 个）：{}".format(len(models), ", ".join(models) or "端点未实现 /models")
        )
    except LLMError as exc:
        print("模型清单不可用（不视为失败）：{}".format(exc))
    try:
        completion = client.complete([{"role": "user", "content": "ping"}])
        print(
            "ping 成功：{!r}（{}ms，usage={}）".format(
                completion.text.strip()[:40], completion.latency_ms, completion.usage
            )
        )
    except LLMError as exc:
        print("ping 失败：{}".format(exc), file=sys.stderr)
        return EXIT_RUN_FAILED
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = args.command or "run"
    try:
        if command == "run":
            return cmd_run(args)
        if command == "plan":
            return cmd_plan(args)
        if command == "graph":
            return cmd_graph(args)
        if command == "report":
            return cmd_report(args)
        if command == "scenarios":
            return cmd_scenarios()
        if command == "probe":
            return cmd_probe(args)
    except ConfigError as exc:
        print("配置错误：{}".format(exc), file=sys.stderr)
        return EXIT_CONFIG
    except AgentError as exc:
        print("输入错误：{}".format(exc), file=sys.stderr)
        return EXIT_CONFIG
    except LLMError as exc:
        print("模型错误：{}".format(exc), file=sys.stderr)
        return EXIT_RUN_FAILED
    parser.print_help()
    return EXIT_CONFIG


__all__ = [
    "EXIT_CONFIG",
    "EXIT_ESCALATED",
    "EXIT_OK",
    "EXIT_RUN_FAILED",
    "build_parser",
    "main",
]
