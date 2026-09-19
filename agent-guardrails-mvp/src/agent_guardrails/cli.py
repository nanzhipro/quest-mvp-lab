"""命令行 —— 只负责装配与产物落盘，不含任何策略判断。

```
guardrails run                      # 全部 6 个场景，离线（脚本化"被劫持模型"，不需要密钥）
guardrails run --live               # 全部场景，真实 DeepSeek 循环
guardrails run --live --scenario indirect_injection --log-level DEBUG
guardrails probe                    # 连通性 + 模型清单自检
guardrails verify runs/<dir>/<scenario>/audit.jsonl   # 复算哈希链
guardrails list                     # 场景清单
```

产物按运行隔离：``runs/<时间戳>_<模式>/<场景>/{agent.jsonl,wire.jsonl,audit.jsonl,result.json}``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from . import __version__, obs
from .audit import iter_audit, verify_chain
from .config import Config, ConfigError, RunPaths
from .guardrails import OutputGuard
from .llm import DeepSeekClient, LLMError
from .runtime import summarise
from .scenarios import SCENARIOS, Scenario, get_scenario, run_scenario, scripted_model

PROG = "guardrails"
EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_RUNTIME_ERROR = 3

DEFAULT_RUNS_DIR = Path("runs")

EXAMPLES = """\
examples:
  guardrails list                                    # 看六个场景分别验证什么
  guardrails run                                     # 离线全量：不需要 API key
  guardrails run --scenario indirect_injection --log-level DEBUG
  guardrails run --live                              # 真实 DeepSeek（需要 DEEPSEEK_API_KEY）
  guardrails probe                                   # 连通性 + 可用模型
  guardrails verify runs/20260916_2015_offline/indirect_injection/audit.jsonl
"""


def _resolve_scenarios(selected: Sequence[str]) -> List[Scenario]:
    if not selected:
        return list(SCENARIOS)
    return [get_scenario(key) for key in selected]


def _run_offline(scenario: Scenario, paths: RunPaths) -> Any:
    return run_scenario(scenario, model=scripted_model(scenario), paths=paths, live=False)


def _run_live(scenario: Scenario, paths: RunPaths, config: Config) -> Any:
    model = DeepSeekClient(config, wire_path=paths.wire)
    return run_scenario(scenario, model=model, paths=paths, live=True)


def cmd_run(args: argparse.Namespace) -> int:
    scenarios = _resolve_scenarios(args.scenario)
    if args.run_dir:
        paths = RunPaths(root=Path(args.run_dir), scenario="-")
        paths.root.mkdir(parents=True, exist_ok=True)
    else:
        paths = RunPaths.fresh(DEFAULT_RUNS_DIR, "live" if args.live else "offline")

    config: Optional[Config] = None
    if args.live:
        try:
            config = Config.from_env(model=args.model)
        except ConfigError as exc:
            print("[配置错误] {}".format(exc), file=sys.stderr)
            return EXIT_CONFIG_ERROR
    else:
        config = None

    runs = []
    for scenario in scenarios:
        scenario_paths = paths.for_scenario(scenario.key).ensure()
        obs.configure_logging(level=args.log_level, jsonl_path=scenario_paths.log, stream=sys.stderr)
        with obs.bind_context(scenario=scenario.key):
            obs.event(
                "run.banner",
                "场景 {}：{}".format(scenario.key, scenario.title),
                threat=scenario.threat,
                mode="live" if args.live else "offline",
                artifacts=str(scenario_paths.dir),
            )
        try:
            run = (
                _run_live(scenario, scenario_paths, config)
                if args.live and config is not None
                else _run_offline(scenario, scenario_paths)
            )
        except LLMError as exc:
            print("[模型错误] {}：{}".format(scenario.key, exc), file=sys.stderr)
            return EXIT_RUNTIME_ERROR
        _write_result(run, scenario_paths)
        runs.append(run)

    offline_summary = summarise([run.result for run in runs])
    payload = {
        "mode": "live" if args.live else "offline",
        "model": config.model if config else "scripted",
        "artifacts": str(paths.root),
        "summary": offline_summary,
        "scenarios": [run.as_dict() for run in runs],
    }
    (paths.root / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _print_report(runs, paths, live=args.live, model=config.model if config else "scripted")
    return EXIT_OK if all(run.ok for run in runs) else EXIT_CHECK_FAILED


def _write_result(run: Any, paths: RunPaths) -> None:
    """结果落盘前先过一遍输出护栏 —— 证据文件本身也不该夹带机密原文。"""
    guard = OutputGuard()
    payload, redactions = guard.sanitize_payload(run.as_dict())
    if redactions:
        payload["artifact_redactions"] = redactions
    paths.result.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    verification = verify_chain(paths.audit)
    obs.event(
        "run.artifacts",
        "产物已落盘",
        dir=str(paths.dir),
        audit_entries=verification.entries,
        audit_chain_ok=verification.ok,
        artifact_redactions=redactions,
    )


def _print_report(runs: Sequence[Any], paths: RunPaths, *, live: bool, model: str) -> None:
    print("\n" + "=" * 96)
    print("护栏实测报告  模式={}  模型={}  产物={}".format("live" if live else "offline", model, paths.root))
    print("=" * 96)
    for run in runs:
        result = run.result
        print(
            "\n[{mark}] {key}｜{title}".format(
                mark="PASS" if run.ok else "FAIL", key=run.scenario.key, title=run.scenario.title
            )
        )
        print(
            "      终态={status} 步骤={steps} 拒绝={denials} 审批(批/拒)={granted}/{refused} "
            "污点={tainted} token={pt}+{ct}".format(
                status=result.status,
                steps=result.steps,
                denials=result.denials,
                granted=result.approvals_granted,
                refused=result.approvals_refused,
                tainted=result.tainted,
                pt=result.prompt_tokens,
                ct=result.completion_tokens,
            )
        )
        for trace in result.tool_traces:
            print(
                "      · step{step} {tool:<14} {decision:<8} rule={rule:<26} {reason}".format(
                    step=trace.step,
                    tool=trace.tool,
                    decision=trace.decision,
                    rule=trace.rule,
                    reason=trace.reason[:80],
                )
            )
        for check in run.checks:
            if not check.ok and check.enforced:
                print("      ✗ {name}: {detail}".format(name=check.name, detail=check.detail))
            elif not check.enforced:
                print("      · 观测 {name}: {detail}".format(name=check.name, detail=check.detail))
    summary = summarise([run.result for run in runs])
    print("\n" + "-" * 96)
    print(
        "汇总：{runs} 次会话｜完成 {completed}｜输入拦截 {blocked}｜预算熔断 {budget_stopped}｜"
        "拒绝动作 {denied_tool_calls}｜审批 批{granted}/拒{refused}｜污点会话 {tainted_runs}｜"
        "token {pt}+{ct}".format(
            runs=summary["runs"],
            completed=summary["completed"],
            blocked=summary["blocked"],
            budget_stopped=summary["budget_stopped"],
            denied_tool_calls=summary["denied_tool_calls"],
            granted=summary["approvals_granted"],
            refused=summary["approvals_refused"],
            tainted_runs=summary["tainted_runs"],
            pt=summary["prompt_tokens"],
            ct=summary["completion_tokens"],
        )
    )
    print(
        "产物：{}（每个场景一个目录：agent.jsonl / wire.jsonl / audit.jsonl / result.json）".format(
            paths.root
        )
    )


def cmd_list(args: argparse.Namespace) -> int:
    print("可用场景（{} 个）：".format(len(SCENARIOS)))
    for scenario in SCENARIOS:
        print("\n· {key}｜{title}".format(key=scenario.key, title=scenario.title))
        print("  目标：{}".format(scenario.goal))
        print("  威胁：{}".format(scenario.threat))
        print("  离线期望：终态={} 规则={}".format(scenario.expect_status, "、".join(scenario.expect_rules)))
        if scenario.notes:
            print("  说明：{}".format(scenario.notes))
    return EXIT_OK


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        config = Config.from_env(model=args.model)
    except ConfigError as exc:
        print("[配置错误] {}".format(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    obs.configure_logging(level="INFO", console=True)
    obs.event(
        "probe.start",
        "连通性自检",
        base_url=config.base_url,
        model=config.model,
        key=config.masked_key(),
    )
    try:
        model = DeepSeekClient(config, wire_path=Path(args.wire) if args.wire else None)
        models = model.list_models()
        completion = model.complete([{"role": "user", "content": "回复 OK 两个字母即可。"}])
    except LLMError as exc:
        print("[连通性失败] {}".format(exc), file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    print("可用模型：{}".format("、".join(models) or "（端点未实现 /models）"))
    print(
        "实测回复：{!r}  延迟={}ms  tokens={}+{}".format(
            completion.text.strip()[:60],
            completion.latency_ms,
            completion.prompt_tokens,
            completion.completion_tokens,
        )
    )
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    verification = verify_chain(Path(args.path))
    print("审计链{}：{}".format("完整" if verification.ok else "已损坏", verification.reason))
    if args.show:
        for entry in iter_audit(Path(args.path)):
            print(json.dumps(entry, ensure_ascii=False)[:200])
    return EXIT_OK if verification.ok else EXIT_CHECK_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="AI Agent 安全护栏 MVP：行动闸门 · 预算熔断 · 污点追踪 · 审计链 · 输出脱敏",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="{} {}".format(PROG, __version__))
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="运行场景（离线默认，--live 接真实模型）")
    run.add_argument("--scenario", action="append", default=[], help="场景 key，可重复；默认全部")
    run.add_argument("--live", action="store_true", help="使用真实 DeepSeek 循环（需要 DEEPSEEK_API_KEY）")
    run.add_argument("--model", default=None, help="覆盖模型名（默认取 DEEPSEEK_MODEL 或 deepseek-flash）")
    run.add_argument("--run-dir", default=None, help="指定产物目录（默认 runs/<时间戳>_<模式>）")
    run.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别；DEBUG 会打印每一步预算与模型往返明细",
    )
    run.set_defaults(func=cmd_run)

    listing = sub.add_parser("list", help="列出场景与各自验证的边界")
    listing.set_defaults(func=cmd_list)

    probe = sub.add_parser("probe", help="模型连通性自检")
    probe.add_argument("--model", default=None)
    probe.add_argument("--wire", default=None, help="把请求/响应原文写入该文件")
    probe.set_defaults(func=cmd_probe)

    verify = sub.add_parser("verify", help="复算审计链哈希（事后证明未被改写）")
    verify.add_argument("path", help="audit.jsonl 路径")
    verify.add_argument("--show", action="store_true", help="同时打印每条记录")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    return int(args.func(args))


__all__ = ["build_parser", "main"]
