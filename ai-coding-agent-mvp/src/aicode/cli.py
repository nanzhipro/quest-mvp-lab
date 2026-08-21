"""Command-line interface for the aicode agent."""

import argparse
import contextlib
import getpass
import io
import json
import os
import sys
from typing import Callable, Dict, List, Optional

from . import __version__
from .agent import Agent, AgentSession, assistant_message
from .api import APIError, ChatClient
from .config import (
    DEFAULT_BASE_URL,
    DEFAULT_CMD_TIMEOUT,
    DEFAULT_MAX_ITERS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    clear_key,
    key_source,
    mask_key,
    resolve_key,
    set_key,
)
from .tools import TOOL_SPECS, ToolRunner

SUBCOMMANDS = {"agent", "chat", "models", "probe", "key"}


def route_argv(argv: List[str]) -> List[str]:
    """Prepend the default `agent` subcommand when the invocation targets a bare task.

    A bare task is detected by looking at the first positional (non-flag) token:
    if it is not a known subcommand, the whole invocation is treated as an
    `agent` run. This makes both `aicode "task"` and `aicode --verbose "task"`
    work, while `aicode probe --model X` keeps its explicit subcommand.
    """
    first_pos = next((a for a in argv if not a.startswith("-")), None)
    if first_pos is not None and first_pos not in SUBCOMMANDS:
        return ["agent", *argv]
    return argv


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", default=DEFAULT_MODEL, help="model id (default: %(default)s)")
    common.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible base URL")
    common.add_argument(
        "--max-iters", type=int, default=DEFAULT_MAX_ITERS, help="agent loop budget"
    )
    common.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    common.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds"
    )
    common.add_argument("--temperature", type=float, default=None)
    common.add_argument(
        "--dir", dest="workdir", default=".", help="working directory (default: cwd)"
    )
    common.add_argument(
        "--dry-run", action="store_true", help="log tool calls without executing them"
    )
    common.add_argument("--reasoning", action="store_true", help="print the model reasoning trace")
    common.add_argument("--verbose", action="store_true")

    parser = argparse.ArgumentParser(
        prog="aicode",
        parents=[common],
        description="Minimal AI coding agent CLI (OpenAI-compatible API).",
        epilog=(
            'The default command is `agent`: `aicode "task"` runs one shot, `aicode` alone '
            "starts a REPL. Global flags go after an explicit subcommand, e.g. "
            "`aicode probe --model X`."
        ),
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command", metavar="{agent,chat,models,probe,key}")

    p_agent = sub.add_parser(
        "agent", parents=[common], help="one-shot coding task; no task = interactive REPL"
    )
    p_agent.add_argument("task", nargs="*", help="task description")

    p_chat = sub.add_parser("chat", parents=[common], help="plain chat, no tools")
    p_chat.add_argument("prompt", nargs="+", help="message text")

    sub.add_parser("models", parents=[common], help="list model ids served by the endpoint")
    sub.add_parser("probe", parents=[common], help="connectivity + tool-calling self-test")

    p_key = sub.add_parser("key", parents=[common], help="manage the stored API key")
    p_key.add_argument("--set", dest="do_set", action="store_true", help="store a new key")
    p_key.add_argument(
        "--keychain",
        action="store_true",
        help="store/read the key in the macOS Keychain instead of the config file",
    )
    p_key.add_argument("--stdin", action="store_true", help="read the key from stdin (with --set)")
    p_key.add_argument(
        "--show", dest="do_show", action="store_true", help="print the masked key and its source"
    )
    p_key.add_argument(
        "--clear", dest="do_clear", action="store_true", help="remove the stored key"
    )
    return parser


def _make_client(args: argparse.Namespace) -> ChatClient:
    return ChatClient(
        base_url=args.base_url,
        api_key=resolve_key(),
        model=args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.timeout,
    )


def _make_runner(args: argparse.Namespace) -> ToolRunner:
    return ToolRunner(workdir=args.workdir, dry_run=args.dry_run, cmd_timeout=DEFAULT_CMD_TIMEOUT)


def _make_agent(args: argparse.Namespace, on_content=None) -> Agent:
    return Agent(
        client=_make_client(args),
        runner=_make_runner(args),
        workdir=args.workdir,
        max_iters=args.max_iters,
        show_reasoning=args.reasoning,
        verbose=args.verbose,
        on_content=on_content,
    )


def _stream_printer() -> tuple:
    """Return (on_content, state): live-print content pieces to stdout.

    ``state["streamed"]`` tells the caller whether anything was printed, so the
    final answer is not duplicated after a streamed run.
    """
    state = {"streamed": False}

    def on_content(piece: str) -> None:
        state["streamed"] = True
        sys.stdout.write(piece)
        sys.stdout.flush()

    return on_content, state


def _fail_api(err: APIError) -> int:
    print("\nAPI error: {}".format(err), file=sys.stderr)
    print("Check --base-url, --model, and `aicode key --show`.", file=sys.stderr)
    return 2


def _cmd_agent(args: argparse.Namespace) -> int:
    task_words = getattr(args, "task", None) or []
    if task_words:
        task = " ".join(task_words)
        on_content, state = _stream_printer()
        try:
            agent = _make_agent(args, on_content=on_content)
            final = agent.run_task(task)
        except APIError as err:
            return _fail_api(err)
        except RuntimeError as err:
            print("error: {}".format(err), file=sys.stderr)
            return 1
        if state["streamed"]:
            print("\n" + "=" * 60)
        else:
            print("\n" + "=" * 60)
            print(final)
        return 0
    return _interactive(args)


def _interactive(args: argparse.Namespace) -> int:
    try:
        on_content, state = _stream_printer()
        session = AgentSession(_make_agent(args, on_content=on_content))
    except RuntimeError as err:
        print("error: {}".format(err), file=sys.stderr)
        return 1
    print("aicode REPL — model={} workdir={}".format(args.model, os.path.abspath(args.workdir)))
    print("Type a task; /exit or Ctrl-D to quit.\n")
    while True:
        try:
            line = input("aicode> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        line = line.strip()
        if not line:
            continue
        if line in {"/exit", "/quit", "/q"}:
            return 0
        try:
            final = session.turn(line)
        except APIError as err:
            _fail_api(err)
            continue
        if state["streamed"]:
            print("\n")
        else:
            print("\n" + final + "\n")


def _cmd_chat(args: argparse.Namespace) -> int:
    prompt_words = getattr(args, "prompt", None) or []
    if not prompt_words:
        print("usage: aicode chat <message>", file=sys.stderr)
        return 1
    on_content, _state = _stream_printer()
    try:
        client = _make_client(args)
        resp = client.chat(
            [{"role": "user", "content": " ".join(prompt_words)}], tools=None, on_content=on_content
        )
    except APIError as err:
        return _fail_api(err)
    except RuntimeError as err:
        print("error: {}".format(err), file=sys.stderr)
        return 1
    if args.reasoning and resp.get("reasoning"):
        print("\n[think] " + resp["reasoning"])
    else:
        print()
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    try:
        client = _make_client(args)
        ids = client.list_models()
    except APIError as err:
        return _fail_api(err)
    except RuntimeError as err:
        print("error: {}".format(err), file=sys.stderr)
        return 1
    print("{} models served at {}".format(len(ids), args.base_url))
    for model_id in sorted(ids):
        marker = "  <-- default" if model_id == args.model else ""
        print(" - {}{}".format(model_id, marker))
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    try:
        client = _make_client(args)
    except RuntimeError as err:
        print("error: {}".format(err), file=sys.stderr)
        return 1
    print("endpoint: {}\nmodel: {}\n".format(args.base_url, args.model))

    try:
        ids = client.list_models()
        present = args.model in ids
        print("[models] {} ids, target {}\n".format(len(ids), "PRESENT" if present else "MISSING"))
        if not present:
            return 2
    except APIError as err:
        return _fail_api(err)

    try:
        resp = client.chat([{"role": "user", "content": "Reply with exactly: pong"}], tools=None)
        print("[chat] reply={!r}\n".format(resp["content"].strip()[:60]))
    except APIError as err:
        return _fail_api(err)

    messages: List[Dict] = [
        {"role": "user", "content": "List the current directory using the list_dir tool."}
    ]
    try:
        resp = client.chat(messages, tools=TOOL_SPECS)
    except APIError as err:
        return _fail_api(err)
    tool_calls = resp.get("tool_calls") or []
    if not tool_calls:
        print("[tools] model returned no tool call — FAILED")
        return 2
    first = tool_calls[0]
    print(
        "[tools] model called {}({})".format(
            first["name"], json.dumps(first["arguments"], ensure_ascii=False)[:80]
        )
    )
    messages.append(assistant_message(resp))
    messages.append(
        {"role": "tool", "tool_call_id": first["id"], "content": "file_a.txt\nfile_b.txt"}
    )
    try:
        resp = client.chat(messages, tools=TOOL_SPECS)
    except APIError as err:
        return _fail_api(err)
    print("[tools] final reply: {!r}".format((resp["content"] or "")[:80]))
    print("\nPROBE OK")
    return 0


def _cmd_key(args: argparse.Namespace) -> int:
    if args.do_set:
        key = sys.stdin.read().strip() if args.stdin else getpass.getpass("API key: ")
        try:
            where = set_key(key, use_keychain=args.keychain)
        except (ValueError, RuntimeError) as err:
            print("error: {}".format(err), file=sys.stderr)
            return 1
        print("Stored in {} — {}".format(where, mask_key(key)))
        return 0
    if args.do_show:
        try:
            key = resolve_key()
        except RuntimeError as err:
            print("error: {}".format(err), file=sys.stderr)
            return 1
        print("source: {}\nkey: {}".format(key_source(), mask_key(key)))
        return 0
    if args.do_clear:
        clear_key()
        print("Stored key removed.")
        return 0
    print("usage: aicode key --set | --show | --clear", file=sys.stderr)
    return 1


HANDLERS: Dict[str, Callable[[argparse.Namespace], int]] = {
    "agent": _cmd_agent,
    "chat": _cmd_chat,
    "models": _cmd_models,
    "probe": _cmd_probe,
    "key": _cmd_key,
}


def main(argv: Optional[List[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    routed = route_argv(raw)
    if routed is raw:
        args = parser.parse_args(raw)
    else:
        # The first parse of a bare-task invocation fails with "invalid choice";
        # swallow that expected error noise before re-parsing the routed argv.
        with contextlib.redirect_stderr(io.StringIO()), contextlib.suppress(SystemExit):
            parser.parse_args(raw)
        args = parser.parse_args(routed)
    if getattr(args, "version", False):
        print("aicode {}".format(__version__))
        return 0
    command = args.command or "agent"
    try:
        return HANDLERS[command](args)
    except KeyboardInterrupt:
        print()
        return 130
