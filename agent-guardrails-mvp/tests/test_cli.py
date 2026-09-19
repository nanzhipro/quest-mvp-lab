"""命令行：路由、退出码、产物落盘，以及"没有密钥时不许假装成功"。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_guardrails.audit import verify_chain
from agent_guardrails.cli import (
    EXIT_CHECK_FAILED,
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    main,
)


def test_help_without_a_command_exits_cleanly(capsys: Any) -> None:
    assert main([]) == EXIT_OK
    assert "guardrails" in capsys.readouterr().out


def test_list_shows_every_scenario_and_what_it_proves(capsys: Any) -> None:
    assert main(["list"]) == EXIT_OK
    out = capsys.readouterr().out
    for key in (
        "happy_path",
        "indirect_injection",
        "destructive_delete",
        "runaway_loop",
        "privilege_escalation",
        "output_redaction",
    ):
        assert key in out
    assert "威胁：" in out


def test_run_offline_writes_artifacts_and_passes(tmp_path: Path, capsys: Any) -> None:
    exit_code = main(["run", "--run-dir", str(tmp_path / "run"), "--log-level", "WARNING"])
    output = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert "[PASS]" in output
    assert "汇总：6 次会话" in output

    run_root = tmp_path / "run"
    assert (run_root / "summary.json").exists()
    for key in ("happy_path", "indirect_injection", "output_redaction"):
        directory = run_root / key
        assert (directory / "agent.jsonl").exists()
        assert (directory / "result.json").exists()
        assert verify_chain(directory / "audit.jsonl").ok


def test_run_artifacts_do_not_carry_secret_material(tmp_path: Path) -> None:
    """证据文件本身也要过输出护栏：留痕不等于原样留存机密。"""
    main(
        ["run", "--scenario", "output_redaction", "--run-dir", str(tmp_path / "run"), "--log-level", "ERROR"]
    )
    result = json.loads((tmp_path / "run" / "output_redaction" / "result.json").read_text("utf-8"))
    assert result["result"]["redactions"] == {"secret.private_key": 1}
    assert "MIIEvQIBADANBgkq" not in json.dumps(result, ensure_ascii=False)


def test_live_without_a_key_is_a_config_error(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main(["run", "--live", "--run-dir", str(tmp_path / "run")]) == EXIT_CONFIG_ERROR


def test_verify_accepts_a_good_chain_and_rejects_a_tampered_one(tmp_path: Path, capsys: Any) -> None:
    main(["run", "--scenario", "happy_path", "--run-dir", str(tmp_path / "run"), "--log-level", "ERROR"])
    audit_path = tmp_path / "run" / "happy_path" / "audit.jsonl"
    assert main(["verify", str(audit_path)]) == EXIT_OK
    assert "链完整" in capsys.readouterr().out

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["fields"]["goal"] = "被人改过的目标"
    lines[0] = json.dumps(payload, ensure_ascii=False)
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main(["verify", str(audit_path)]) == EXIT_CHECK_FAILED
    assert "已损坏" in capsys.readouterr().out


def test_verify_show_prints_records_without_rewriting_the_file(tmp_path: Path, capsys: Any) -> None:
    main(["run", "--scenario", "happy_path", "--run-dir", str(tmp_path / "run"), "--log-level", "ERROR"])
    audit_path = tmp_path / "run" / "happy_path" / "audit.jsonl"
    before = audit_path.read_text(encoding="utf-8")
    assert main(["verify", str(audit_path), "--show"]) == EXIT_OK
    assert "run.start" in capsys.readouterr().out
    assert audit_path.read_text(encoding="utf-8") == before


def test_unknown_scenario_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        main(["run", "--scenario", "nope", "--run-dir", str(tmp_path / "run")])
