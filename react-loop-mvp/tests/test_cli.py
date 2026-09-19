"""cli.py — argument routing, offline runs, exit codes, and the artifacts on disk."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any, List, Optional

import pytest

from react_loop_mvp import __version__
from react_loop_mvp.cli import EXIT_CONFIG_ERROR, EXIT_OK, EXIT_RUN_FAILED, main


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def transport(outcomes: List[Any]):
    queue = list(outcomes)

    def _call(request: Any, timeout: Optional[float] = None) -> Any:
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _call


@pytest.fixture(autouse=True)
def _no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from 'no credentials configured' unless it says otherwise."""
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(name, raising=False)


# ── offline demo (no key, no network) ────────────────────────────────────────
def test_fake_mode_runs_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "es-mvp" in captured.out
    assert "SPEC.md" in captured.out
    assert "Final Answer:" in captured.err  # the trace goes to stderr


def test_fake_mode_works_for_both_modes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake", "--mode", "native"]) == EXIT_OK
    assert "反向静音" in capsys.readouterr().out


def test_explicit_task_overrides_the_bundled_one(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake", "算一下", "2+2"]) == EXIT_OK
    assert "es-mvp" in capsys.readouterr().out


def test_json_output_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--fake", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "react"
    assert payload["stopped_reason"] == "final_answer"
    assert payload["tool_calls"] == 2
    assert payload["answer"].startswith("项目是 es-mvp")


def test_task_can_be_piped_on_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps(["Final Answer: 来自 stdin 的回答"]), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("算一下 1+1"))
    assert main(["--script", str(script)]) == EXIT_OK
    assert "来自 stdin 的回答" in capsys.readouterr().out


def test_missing_task_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = tmp_path / "bare.json"  # a plain list has no bundled question to fall back on
    script.write_text(json.dumps(["Final Answer: x"]), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as excinfo:
        main(["--script", str(script), ""])
    assert excinfo.value.code == 2


def test_missing_script_file_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--script", str(tmp_path / "nope.json"), "任务"]) == EXIT_CONFIG_ERROR
    assert "script file not found" in capsys.readouterr().err


def test_script_without_the_requested_mode_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "react_only.json"
    script.write_text(json.dumps({"react": ["Final Answer: x"]}), encoding="utf-8")
    assert main(["--script", str(script), "--mode", "native", "任务"]) == EXIT_CONFIG_ERROR
    assert "no 'native' section" in capsys.readouterr().err


# ── live path (no network either: urlopen is patched) ────────────────────────
def test_missing_api_key_exits_with_the_config_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["随便问一句"]) == EXIT_CONFIG_ERROR
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err


def test_live_run_calls_the_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        transport(
            [
                FakeResponse(
                    {
                        "choices": [
                            {"message": {"content": "Final Answer: 42"}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                    }
                )
            ]
        ),
    )
    assert main(["--json", "answer with no tools"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer"] == "42"
    assert payload["usage_totals"] == {"prompt_tokens": 11, "completion_tokens": 3}


def test_model_error_is_reported_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        transport([urllib.error.HTTPError("u", 401, "unauthorized", {}, io.BytesIO(b"{}"))]),
    )
    assert main(["问题"]) == EXIT_RUN_FAILED
    assert "model error" in capsys.readouterr().err


def test_budget_exhaustion_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-test")
    never_finishes = FakeResponse(
        {"choices": [{"message": {"content": "Action: calculator\nAction Input: 1+1"}}]}
    )
    monkeypatch.setattr("urllib.request.urlopen", transport([never_finishes] * 6))
    assert main(["--max-steps", "2", "打转"]) == EXIT_RUN_FAILED
    assert "no final answer" in capsys.readouterr().err


# ── artifacts ────────────────────────────────────────────────────────────────
def test_trace_dir_receives_result_trace_and_wire(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "runs" / "001"
    assert main(["--fake", "--trace-dir", str(target)]) == EXIT_OK
    capsys.readouterr()

    result = json.loads((target / "result.json").read_text(encoding="utf-8"))
    assert result["stopped_reason"] == "final_answer"
    assert result["steps"][0]["action"] == "search_docs"

    events = [
        json.loads(line)
        for line in (target / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert next(entry["event"] for entry in events) == "task"
    assert "stop" in [entry["event"] for entry in events]
    assert events[1]["prompt"].startswith("可用工具")

    wire = [
        json.loads(line)
        for line in (target / "wire.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(wire) == 3  # one exchange per model turn
    assert wire[0]["endpoint"].startswith("scripted://")
    assert wire[0]["run_id"].endswith("_react")


def test_native_trace_dir_writes_native_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "runs" / "native"
    assert main(["--fake", "--mode", "native", "--trace-dir", str(target)]) == EXIT_OK
    capsys.readouterr()
    result = json.loads((target / "result.json").read_text(encoding="utf-8"))
    assert result["mode"] == "native"
    assert result["messages"][0]["role"] == "system"


# ── probe / version ──────────────────────────────────────────────────────────
def test_version_verb(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == EXIT_OK
    assert __version__ in capsys.readouterr().out


def test_probe_reports_the_endpoint_and_a_ping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-probe-test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        transport(
            [
                FakeResponse({"data": [{"id": "deepseek-chat"}]}),
                FakeResponse(
                    {
                        "choices": [{"message": {"content": "pong"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }
                ),
            ]
        ),
    )
    assert main(["probe"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "endpoint : https://api.deepseek.com" in out
    assert "deepseek-chat" in out
    assert "****test" in out  # the key never appears in full
    assert "sk-probe-test" not in out


def test_probe_reports_a_chat_failure_as_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-probe-test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        transport(
            [
                FakeResponse({"data": []}),
                urllib.error.HTTPError(
                    "u", 401, "unauthorized", {}, io.BytesIO(b'{"error":"bad key"}')
                ),
            ]
        ),
    )
    assert main(["probe"]) == EXIT_RUN_FAILED
    assert "error    :" in capsys.readouterr().out


def test_probe_without_a_key_is_a_config_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["probe"]) == EXIT_CONFIG_ERROR
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err


def test_probe_tolerates_a_missing_models_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-probe-test")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        transport(
            [
                urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b"{}")),
                FakeResponse({"choices": [{"message": {"content": "pong"}}]}),
            ]
        ),
    )
    assert main(["probe"]) == EXIT_OK
    assert "unavailable" in capsys.readouterr().out


def test_help_is_available() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_trace_dir_is_created_with_a_timestamped_run_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "runs" / "ts"
    main(["--fake", "--trace-dir", str(target)])
    capsys.readouterr()
    wire = json.loads((target / "wire.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(wire["run_id"]) == len("20260916_153000_react")
    assert wire["run_id"].endswith("_react")


def test_json_keys_are_stable() -> None:
    """The structured output is a contract: other tools parse it."""
    from react_loop_mvp.llm import ScriptedClient
    from react_loop_mvp.react import ReActAgent
    from react_loop_mvp.tools import default_registry

    result = ReActAgent(
        ScriptedClient(["Action: calculator\nAction Input: 1+1", "Final Answer: 2"]),
        default_registry(),
    ).run("q")
    assert set(result.as_dict()) == {
        "mode",
        "task",
        "answer",
        "stopped_reason",
        "steps",
        "tool_calls",
        "elapsed_ms",
        "usage_totals",
        "last_step_prompt_chars",
    }
    assert set(result.steps[0].as_dict()) >= {"step", "thought", "action", "observation", "ok"}
