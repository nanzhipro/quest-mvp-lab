"""Tests for aicode.cli — argv routing and parser wiring."""

import argparse

from aicode import cli


def test_sniff_routes_bare_task_to_agent():
    assert cli.route_argv(["write a file"]) == ["agent", "write a file"]


def test_sniff_keeps_known_subcommands():
    assert cli.route_argv(["probe", "--model", "x"]) == ["probe", "--model", "x"]
    assert cli.route_argv(["chat", "hi"]) == ["chat", "hi"]


def test_flags_before_task_route_to_agent():
    assert cli.route_argv(["--dry-run", "task"]) == ["agent", "--dry-run", "task"]
    assert cli.route_argv(["--verbose", "task", "here"]) == ["agent", "--verbose", "task", "here"]


def test_sniff_empty_and_flags_only():
    assert cli.route_argv([]) == []
    assert cli.route_argv(["--version"]) == ["--version"]


def test_sniff_flag_value_before_task_routes_to_agent():
    assert cli.route_argv(["--model", "m2", "task"]) == ["agent", "--model", "m2", "task"]


def test_parser_bare_task_defaults_to_agent():
    args = cli.build_parser().parse_args(["agent", "create", "hello.py"])
    assert args.command == "agent"
    assert args.task == ["create", "hello.py"]
    assert args.model == "qwen3.8-27b"
    assert args.max_iters == 20


def test_parser_no_args_exposes_common_options():
    """Bare `aicode` must still carry the common options (REPL path needs them)."""
    args = cli.build_parser().parse_args([])
    assert args.command is None
    assert args.model == "qwen3.8-27b"
    assert args.base_url.endswith("/compatible-mode/v1")
    assert args.max_iters == 20
    assert args.dry_run is False


def test_parser_agent_flags():
    args = cli.build_parser().parse_args(
        ["agent", "--dry-run", "--model", "m2", "--max-iters", "3", "task"]
    )
    assert args.dry_run is True
    assert args.model == "m2"
    assert args.max_iters == 3
    assert args.timeout == 300


def test_parser_chat_and_key_and_probe():
    parser = cli.build_parser()
    assert parser.parse_args(["chat", "--reasoning", "hello", "world"]).prompt == [
        "hello",
        "world",
    ]
    assert parser.parse_args(["key", "--show"]).do_show is True
    assert parser.parse_args(["key", "--set", "--keychain"]).keychain is True
    assert parser.parse_args(["probe"]).command == "probe"
    assert parser.parse_args(["models"]).command == "models"


def test_version_flag(capsys):
    code = cli.main(["--version"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.strip().startswith("aicode 0.1.0")


def test_cmd_agent_without_task_attribute_starts_interactive(monkeypatch):
    """`aicode` with zero args routes to the agent handler with no `task` attribute."""
    called = {}

    def fake_interactive(_args):
        called["x"] = True
        return 0

    monkeypatch.setattr(cli, "_interactive", fake_interactive)
    args = argparse.Namespace()
    assert cli._cmd_agent(args) == 0
    assert called == {"x": True}


def test_chat_without_prompt_prints_usage(capsys):
    args = argparse.Namespace()
    code = cli._cmd_chat(args)
    assert code == 1
    assert "usage" in capsys.readouterr().err


def test_key_subcommand_without_action_errors(capsys):
    code = cli.main(["key"])
    assert code == 1
    assert "usage" in capsys.readouterr().err
