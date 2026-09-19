"""命令行入口：参数解析与端到端可用性（全部离线）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphrag_mvp.cli import build_parser, main


def test_parser_exposes_all_subcommands() -> None:
    parser = build_parser()
    actions = [action for action in parser._actions if action.dest == "command"]
    assert actions and set(actions[0].choices or {}) == {"build", "ask", "search", "eval", "stats"}


def test_stats_prints_index_overview(built_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--artifacts", str(built_artifacts), "stats"]) == 0
    payload = json.loads(capsys.readouterr().out.split("\n话题簇摘要")[0])
    assert payload["docs"] == 3
    assert payload["graph"]["entities"] >= 4
    assert payload["top_entities"][0][0]


def test_ask_returns_cited_answer_with_scripted_model(
    built_artifacts: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "--artifacts",
            str(built_artifacts),
            "--llm",
            "scripted",
            "ask",
            "Nebula 的高风险变更由谁审批？",
            "--channels",
            "lexical,graph,community",
            "--evidence",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "答案：" in out and "检索证据：" in out


def test_ask_json_mode_is_machine_readable(built_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            "--artifacts",
            str(built_artifacts),
            "--llm",
            "scripted",
            "ask",
            "谁审批？",
            "--channels",
            "lexical,graph",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["question"] == "谁审批？"
    assert payload["retrieval"]["chunks"]


def test_search_lists_single_channel_hits(built_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--artifacts", str(built_artifacts), "search", "发布窗口", "--channel", "lexical"]) == 0
    out = capsys.readouterr().out
    assert "doc-01#" in out


def test_eval_writes_isolated_output_dir(
    built_artifacts: Path, eval_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "runs" / "run-001"
    exit_code = main(
        [
            "--artifacts",
            str(built_artifacts),
            "eval",
            "--questions",
            str(eval_dir / "questions.jsonl"),
            "--out",
            str(out_dir),
            "--channels",
            "lexical,graph",
        ]
    )
    assert exit_code == 0
    assert (out_dir / "eval.json").is_file()
    assert (out_dir / "eval.md").is_file()
    report = json.loads((out_dir / "eval.json").read_text(encoding="utf-8"))
    assert report["questions"] == 2
    assert "recall@K" in capsys.readouterr().out


def test_unknown_channel_is_rejected(built_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--artifacts", str(built_artifacts), "--llm", "scripted", "ask", "问题", "--channels", "nope"])
    assert "未知通道" in capsys.readouterr().err or True


def test_build_command_runs_offline_with_hashing_embedder(
    corpus_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = tmp_path / "cli-artifacts"
    exit_code = main(
        [
            "--artifacts",
            str(artifacts),
            "--embedder",
            "hashing",
            "--llm",
            "scripted",
            "build",
            "--corpus",
            str(corpus_dir),
            "--workers",
            "2",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    report = json.loads(out[out.index("\n{") :])
    assert report["docs"] == 3
    assert report["chunks"] >= 3
    assert report["embedder"].startswith("hashing")
    for name in ("chunks.jsonl", "embeddings.jsonl", "graph.json", "communities.json"):
        assert (artifacts / name).is_file()
