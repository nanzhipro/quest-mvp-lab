"""Command line — wiring only. No loop logic, no prompt text, no tool logic here.

Routing rule (documented because it is unusual): ``react-loop "question"`` is the
default action, and the two administrative verbs ``probe`` and ``version`` are
recognised when they are the *first* argument. That keeps the common case
(``react-loop "why is the sky blue"``) free of subcommand ceremony.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .config import Config, ConfigError
from .llm import ChatModel, DeepSeekClient, LLMError, ScriptedClient
from .native import NativeAgent
from .react import ReActAgent, result_json
from .tools import ToolRegistry, default_registry
from .trace import ConsoleTracer, JsonlTracer, MultiTracer, Tracer

PROG = "react-loop"
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CONFIG_ERROR = 2

EXAMPLES = """\
examples:
  react-loop "3 * (4 + 5) 等于多少？"                     # ReAct 文本协议（默认）
  react-loop --mode native "同上"                          # 原生 function calling 对照
  react-loop --fake                                       # 离线演示：不需要 API key
  react-loop --script my_script.json "换个脚本重放"         # 自定义脚本化模型
  react-loop --show-prompt "看看每一步发出去的完整上下文"
  react-loop --trace-dir runs/demo "问题"                  # 落盘 trace/wire/result 三件产物
  react-loop probe                                        # 连通性 + 模型清单自检
"""


def _stdin_task() -> str:
    """Read the task from a pipe; never block or blow up when stdin is unusable."""
    stream = sys.stdin
    if stream is None:
        return ""
    try:
        if stream.isatty():
            return ""
        return stream.read().strip()
    except (OSError, ValueError, AttributeError):
        return ""


def _bundled_script() -> Dict[str, Any]:
    raw = (
        resources.files("react_loop_mvp")
        .joinpath("data/demo_script.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


def _load_script(value: str) -> Tuple[Any, Dict[str, Any]]:
    """``--fake`` with no value → bundled demo; with a path → that JSON file.

    The payload is either a plain list of replies (mode-agnostic) or a mapping with
    per-mode sections (``react`` / ``native``) plus descriptive keys.
    """
    if value:
        path = Path(value).expanduser()
        if not path.is_file():
            raise ConfigError("script file not found: {}".format(path))
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = _bundled_script()
    if isinstance(payload, dict):
        meta = {key: item for key, item in payload.items() if key not in ("react", "native")}
    else:
        meta = {}
    return payload, meta


def _scripted_replies(payload: Any, mode: str) -> List[Any]:
    if isinstance(payload, list):
        return list(payload)
    replies = payload.get(mode)
    if replies is None:
        raise ConfigError("script has no '{}' section".format(mode))
    return list(replies)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="手写 20 行 ReAct 循环（DeepSeek / OpenAI 兼容端点）",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", nargs="*", help="要问的问题；省略时从 stdin 读取")
    parser.add_argument(
        "--mode",
        choices=("react", "native"),
        default="react",
        help="react = 手写文本协议（默认）；native = 原生 function calling",
    )
    parser.add_argument("--model", help="模型名（默认取环境变量 {}）".format("DEEPSEEK_MODEL"))
    parser.add_argument("--base-url", help="OpenAI 兼容端点（默认 https://api.deepseek.com）")
    parser.add_argument("--max-steps", type=int, default=8, help="最大轮数（默认 8）")
    parser.add_argument(
        "--temperature", type=float, default=0.0, help="采样温度（默认 0.0，便于复现）"
    )
    parser.add_argument("--max-tokens", type=int, default=None, help="单轮回复上限")
    parser.add_argument(
        "--fake", action="store_true", help="离线演示：用内置脚本化模型替代真实模型，不需要 API key"
    )
    parser.add_argument(
        "--script", metavar="SCRIPT.json", help="用自定义脚本文件替代真实模型（同样不需要 key）"
    )
    parser.add_argument(
        "--trace-dir", metavar="DIR", help="把 trace.jsonl / wire.jsonl / result.json 写到这里"
    )
    parser.add_argument("--show-prompt", action="store_true", help="打印每一步完整 prompt")
    parser.add_argument("--show-reasoning", action="store_true", help="打印模型推理字段（如有）")
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="stdout 只输出结构化结果"
    )
    parser.add_argument("--version", action="version", version="{} {}".format(PROG, __version__))
    return parser


def _probe_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="{} probe".format(PROG), description="连通性与模型自检")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--json", dest="as_json", action="store_true")
    return parser


def build_model(args: argparse.Namespace) -> Tuple[ChatModel, Dict[str, Any], str]:
    """Return ``(model, script_meta, label)`` — the only place the CLI branches on --fake."""
    if args.fake or args.script:
        payload, meta = _load_script(args.script or "")
        replies = _scripted_replies(payload, args.mode)
        return ScriptedClient(replies), meta, "scripted ({})".format(args.script or "bundled demo")
    config = Config.from_env(base_url=args.base_url, model=args.model)
    client = DeepSeekClient(config, temperature=args.temperature, max_tokens=args.max_tokens)
    return client, {}, "{} @ {}".format(config.model, config.base_url)


def _write_artifacts(
    directory: Path, result: Any, model: ChatModel, run_id: str, mode: str
) -> List[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    result_path = directory / "result.json"
    result_path.write_text(
        result_json(result) if mode == "react" else _pretty(result.as_dict()), encoding="utf-8"
    )
    written.append(result_path)
    wire_path = directory / "wire.jsonl"
    with wire_path.open("w", encoding="utf-8") as handle:
        for index, exchange in enumerate(model.exchanges, start=1):
            handle.write(
                json.dumps(
                    {"run_id": run_id, "index": index, "mode": mode, **exchange},
                    ensure_ascii=False,
                )
                + "\n"
            )
    written.append(wire_path)
    return written


def _pretty(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _run(argv: Sequence[str]) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv))
    task = " ".join(args.task).strip() or _stdin_task()

    try:
        model, meta, label = build_model(args)
    except ConfigError as exc:
        print("config error: {}".format(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if not task and meta.get("task"):  # an offline demo carries its own question
        task = str(meta["task"])
    if not task:
        parser.error("no task given — pass it as an argument or pipe it on stdin")

    task_for_run = task

    run_id = "{}_{}".format(time.strftime("%Y%m%d_%H%M%S"), args.mode)
    tracers: List[Tracer] = [
        ConsoleTracer(show_prompt=args.show_prompt, show_reasoning=args.show_reasoning)
    ]
    if args.trace_dir:
        tracers.append(
            JsonlTracer(Path(args.trace_dir).expanduser() / "trace.jsonl", run_id=run_id)
        )
    tracer = MultiTracer(tracers)
    tracer.record("task", task=task_for_run, mode=args.mode, model=label)

    registry: ToolRegistry = default_registry()
    agent = (
        ReActAgent(model, registry, max_steps=args.max_steps, tracer=tracer)
        if args.mode == "react"
        else NativeAgent(model, registry, max_steps=args.max_steps, tracer=tracer)
    )
    try:
        result = agent.run(task_for_run)
    except LLMError as exc:
        tracer.record("error", message=str(exc))
        tracer.close()
        print("model error: {}".format(exc), file=sys.stderr)
        return EXIT_RUN_FAILED
    finally:
        tracer.close()

    if args.trace_dir:
        for path in _write_artifacts(
            Path(args.trace_dir).expanduser(), result, model, run_id, args.mode
        ):
            print("wrote {}".format(path), file=sys.stderr)

    if args.as_json:
        print(_pretty(result.as_dict()))
    elif result.answer is not None:
        print(result.answer)
    else:
        print(
            "no final answer (stopped: {}); last reply:\n{}".format(
                result.stopped_reason, getattr(result, "last_text", "")
            ),
            file=sys.stderr,
        )
    return EXIT_OK if result.succeeded else EXIT_RUN_FAILED


def _probe(argv: Sequence[str]) -> int:
    args = _probe_parser().parse_args(list(argv))
    try:
        config = Config.from_env(base_url=args.base_url, model=args.model)
    except ConfigError as exc:
        print("config error: {}".format(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    client = DeepSeekClient(config, temperature=0.0, max_tokens=32)
    report: Dict[str, Any] = {
        "base_url": config.base_url,
        "model": config.model,
        "api_key": config.masked_key(),
        "models": None,
        "ping": None,
        "error": None,
    }
    try:
        report["models"] = client.list_models()
    except LLMError as exc:
        report["models"] = "unavailable: {}".format(exc)
    try:
        reply = client.complete([{"role": "user", "content": "ping"}])
        report["ping"] = {
            "text": reply.text.strip(),
            "latency_ms": reply.latency_ms,
            "usage": reply.usage,
        }
    except LLMError as exc:
        report["error"] = str(exc)
    if args.as_json:
        print(_pretty(report))
    else:
        print("endpoint : {}".format(report["base_url"]))
        print("model    : {}".format(report["model"]))
        print("api key  : {}".format(report["api_key"]))
        print("models   : {}".format(report["models"]))
        print("ping     : {}".format(report["ping"]))
        if report["error"]:
            print("error    : {}".format(report["error"]))
    return EXIT_OK if report["error"] is None else EXIT_RUN_FAILED


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for both ``react-loop`` and ``python -m react_loop_mvp``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "probe":
        return _probe(args[1:])
    if args and args[0] in ("version", "--version", "-V"):
        print("{} {}".format(PROG, __version__))
        return EXIT_OK
    return _run(args)


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
