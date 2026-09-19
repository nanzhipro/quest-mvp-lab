"""Report-rendering tests: the HTML must be self-contained, drillable, and truthful."""

from __future__ import annotations

import json
from pathlib import Path

from supervisor_graph_mvp.demo import load_script
from supervisor_graph_mvp.llm import ScriptedClient
from supervisor_graph_mvp.pipeline import Orchestrator
from supervisor_graph_mvp.reporting import render_ruling, result_payload, summarize
from supervisor_graph_mvp.scenarios import find_scenario
from supervisor_graph_mvp.viz import (
    build_report_html,
    read_jsonl,
    report_from_run_dir,
    write_report,
)


def _payload(scenario: str = "S4-block-p4") -> dict:
    orchestrator = Orchestrator(ScriptedClient(load_script(scenario), label=scenario))
    return result_payload(orchestrator.run(find_scenario(scenario)))


def test_html_is_self_contained_and_escapes_nothing_it_should_not():
    html = build_report_html(_payload())
    assert html.startswith("<!doctype html>")
    assert "src=" not in html.replace('src="', "")  # no external script tags
    assert "@media (prefers-color-scheme: dark)" in html  # CSS braces survived .format()
    assert "function show(title, value)" in html  # JS braces survived too
    assert "<script>" in html and "</script>" in html


def test_html_embeds_the_run_payload_for_drilldown():
    payload = _payload()
    html = build_report_html(payload)
    assert "const DATA = " in html
    assert '"verdict": "block"' in html
    for node in ("intake", "classify", "plan", "dispatch", "check", "aggregate"):
        assert 'data-node="{}"'.format(node) in html


def test_html_shows_the_consistency_findings_with_their_repairs():
    html = build_report_html(_payload())
    assert "C5-citation" in html
    assert "一致性问题" in html


def test_html_header_numbers_come_from_the_payload():
    payload = _payload()
    html = build_report_html(payload)
    assert "<b>{}</b><span>修复轮次</span>".format(payload["repairs"]) in html
    assert "<b>{}</b><span>模型调用</span>".format(len(payload["llm_calls"])) in html


def test_html_escapes_hostile_text():
    payload = _payload()
    payload["request"]["text"] = "<script>alert(1)</script>"
    html = build_report_html(payload)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_write_report_creates_parent_directories(tmp_path: Path):
    target = write_report(tmp_path / "nested" / "report.html", _payload())
    assert target.is_file() and target.parent.is_dir()


def test_read_jsonl_skips_blanks_and_broken_lines(tmp_path: Path):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"a": 1}\n\nnot json\n{"b": 2}\n', encoding="utf-8")
    assert read_jsonl(path) == [{"a": 1}, {"b": 2}]
    assert read_jsonl(tmp_path / "missing.jsonl") == []


def test_report_from_run_dir_rebuilds_from_evidence(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    payload = _payload()
    (run_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (run_dir / "trace.jsonl").write_text('{"event": "run_start"}\n', encoding="utf-8")
    (run_dir / "wire.jsonl").write_text(
        json.dumps({"request": {"body": {"messages": []}}, "response_raw": "{}"}) + "\n",
        encoding="utf-8",
    )
    html_path = report_from_run_dir(run_dir)
    assert html_path == run_dir / "report.html"
    html = html_path.read_text(encoding="utf-8")
    assert "run_start" in html and "response_raw" in html
    explicit = report_from_run_dir(run_dir, out=tmp_path / "custom.html")
    assert explicit.is_file()


def test_render_ruling_is_markdown_with_the_verdict_first():
    payload = _payload("S4-block-p4")
    text = render_ruling(payload["ruling"])
    assert text.startswith("## 合规裁决：block（阻断）")
    assert "| 资产级别 | P4 |" in text
    assert "### 处置动作" in text
    assert "阻断该外发通道" in text


def test_render_ruling_shows_the_escalation_block():
    payload = _payload("S3-missing-evidence")
    text = render_ruling(payload["ruling"])
    assert "### 升级人工" in text
    assert "待人工处理" in text


def test_summarize_is_one_line():
    orchestrator = Orchestrator(ScriptedClient(load_script("S1-public-allow"), label="x"))
    run_state = orchestrator.run(find_scenario("S1-public-allow"))
    line = summarize(run_state)
    assert line.count("\n") == 0
    assert "allow" in line and "步" in line


def test_result_payload_keys_are_stable():
    payload = _payload("S1-public-allow")
    assert set(payload) == {
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
    assert payload["graph"]["name"] == "compliance-orchestration"
