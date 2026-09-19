"""CLI tests: exit codes, stdout/stderr split, and the evidence files a run leaves behind."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor_graph_mvp.cli import main

RUN_KEYS = {
    "request",
    "options",
    "intent",
    "plan",
    "results",
    "findings",
    "consistency",
    "ruling",
    "narrative",
    "verdict",
    "status",
    "repairs",
    "repair_log",
    "llm_calls",
    "graph",
    "visits",
}


def test_run_fake_prints_a_ruling_and_exits_zero(capsys):
    code = main(["run", "S1-public-allow", "--fake"])
    captured = capsys.readouterr()
    assert code == 0
    assert "合规裁决：allow" in captured.out
    assert "资产级别 | P1" in captured.out
    assert "运行" in captured.err  # the trace goes to stderr


def test_run_quiet_silences_the_trace(capsys):
    main(["run", "S1-public-allow", "--fake", "--quiet"])
    captured = capsys.readouterr()
    assert "▶" not in captured.err
    assert "合规裁决" in captured.out


def test_run_json_has_the_stable_top_level_contract(capsys):
    code = main(["run", "S2-plan-gap", "--fake", "--json", "--quiet"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == RUN_KEYS
    assert payload["verdict"] == "review"
    assert payload["repairs"] == 1


def test_escalation_uses_the_dedicated_exit_code(capsys):
    code = main(["run", "S3-missing-evidence", "--fake", "--json", "--quiet"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["status"] == "escalated"
    assert payload["ruling"]["escalation"]["asked_of_human"]


def test_trace_dir_writes_all_three_evidence_files(tmp_path: Path, capsys):
    code = main(["run", "S4-block-p4", "--fake", "--trace-dir", str(tmp_path), "--quiet"])
    assert code == 0
    run_dir = next(path for path in tmp_path.iterdir() if path.name.endswith("S4-block-p4"))
    for name in ("trace.jsonl", "wire.jsonl", "result.json"):
        assert (run_dir / name).is_file()
    payload = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "block"
    events = [
        json.loads(line)["event"]
        for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    ]
    assert events.count("node_exit") == len(payload["visits"])
    assert events[0] == "run_start" and events[-1] == "graph_stop"
    assert {"intent", "plan", "subtask", "consistency", "ruling"} <= set(events)


def test_wire_jsonl_never_contains_a_key(tmp_path: Path):
    main(["run", "S1-public-allow", "--fake", "--trace-dir", str(tmp_path), "--quiet"])
    run_dir = next(path for path in tmp_path.iterdir() if path.name.endswith("S1-public-allow"))
    wire = (run_dir / "wire.jsonl").read_text(encoding="utf-8")
    assert "sk-" not in wire
    assert "Authorization" not in wire


def test_html_report_is_written_and_self_contained(tmp_path: Path, capsys):
    target = tmp_path / "report.html"
    code = main(["run", "S4-block-p4", "--fake", "--html", str(target), "--quiet"])
    assert code == 0
    html = target.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "http://" not in html and "https://" not in html
    assert "DATA = " in html
    assert "block" in html


def test_dump_prompts_prints_the_system_prompt(capsys):
    main(["run", "S1-public-allow", "--fake", "--dump-prompts", "--quiet"])
    captured = capsys.readouterr()
    assert "[prompt #0.system]" in captured.err
    assert "意图识别" in captured.err


def test_run_all_covers_every_scenario(tmp_path: Path, capsys):
    code = main(["run", "--all", "--fake", "--trace-dir", str(tmp_path), "--quiet"])
    out = capsys.readouterr().out
    assert code == 0
    for scenario in (
        "S1-public-allow",
        "S2-plan-gap",
        "S3-missing-evidence",
        "S4-block-p4",
        "S5-plan-missing-agent",
    ):
        assert scenario in out
    assert len(list(tmp_path.iterdir())) == 5


def test_plan_command_prints_intent_and_table(capsys):
    code = main(["plan", "S2-plan-gap", "--fake"])
    out = capsys.readouterr().out
    assert code == 0
    assert "意图：" in out
    assert "计划来源：llm" in out
    assert "| t1 | classification |" in out


def test_plan_command_json_mode(capsys):
    main(["plan", "S2-plan-gap", "--fake", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"request", "intent", "plan"}
    assert payload["plan"]["subtasks"][3]["depends_on"] == []


def test_graph_command_table_and_mermaid(capsys):
    assert main(["graph"]) == 0
    table = capsys.readouterr().out
    assert "| check |" in table and "一致性检查" in table
    assert main(["graph", "--format", "mermaid"]) == 0
    mermaid = capsys.readouterr().out
    assert mermaid.startswith("flowchart TD")
    assert "check -->|repair| dispatch" in mermaid


def test_scenarios_command_lists_the_packaged_requests(capsys):
    assert main(["scenarios"]) == 0
    out = capsys.readouterr().out
    assert "S1-public-allow" in out
    assert "离线可重放" in out


def test_report_command_rebuilds_html_from_a_run_directory(tmp_path: Path, capsys):
    main(["run", "S1-public-allow", "--fake", "--trace-dir", str(tmp_path), "--quiet"])
    run_dir = next(path for path in tmp_path.iterdir() if path.name.endswith("S1-public-allow"))
    assert main(["report", str(run_dir)]) == 0
    printed = [line for line in capsys.readouterr().out.strip().splitlines() if line.strip()][-1]
    assert printed.endswith("report.html")
    assert Path(printed).is_file()


def test_unknown_scenario_is_an_input_error(capsys):
    assert main(["run", "does-not-exist", "--fake"]) == 2
    err = capsys.readouterr().err
    assert "输入错误" in err and "no scenario matches" in err


def test_fake_without_a_scenario_is_a_configuration_error(capsys):
    assert main(["run", "--fake"]) == 2
    assert "scenario id" in capsys.readouterr().err


def test_missing_api_key_is_a_configuration_error(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main(["run", "S1-public-allow"]) == 2
    assert "配置错误" in capsys.readouterr().err


def test_probe_without_a_key_reports_configuration(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main(["probe"]) == 2
    assert "配置错误" in capsys.readouterr().err


def test_version_flag_exits(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert "supervisor-graph-mvp" in capsys.readouterr().out


def test_custom_request_file_is_accepted(tmp_path: Path, capsys):
    request = {
        "id": "ad-hoc",
        "text": "请裁决这次外发",
        "asset": {
            "path": "finance-export-2026-0919.csv",
            "channel": "webmail",
            "destination_type": "external",
        },
        "evidence": [{"id": "rule:R-03", "kind": "rule"}],
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    code = main(
        [
            "plan",
            "--request",
            str(path),
            "--fake",
            "--script-file",
            str(_script_for_ad_hoc(tmp_path)),
        ]
    )
    assert code == 0
    assert "意图：" in capsys.readouterr().out


def _script_for_ad_hoc(tmp_path: Path) -> Path:
    import json as _json

    script = {
        "script": [
            _json.dumps(
                {
                    "intents": ["policy_applicability"],
                    "primary": "policy_applicability",
                    "reason": "x",
                }
            ),
            _json.dumps(
                {
                    "subtasks": [
                        {"id": "t1", "agent": "policy", "goal": "判策略", "depends_on": []}
                    ],
                    "rationale": "最小计划",
                }
            ),
        ]
    }
    path = tmp_path / "script.json"
    path.write_text(_json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return path
