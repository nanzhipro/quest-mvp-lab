"""评测层：指标口径、消融报告与答案级评分。"""

from __future__ import annotations

from pathlib import Path

from graphrag_mvp.evaluate import (
    QuestionRecord,
    evaluate,
    load_questions,
    score_answer,
)
from graphrag_mvp.retriever import ALL_CHANNELS


def test_load_questions_reads_jsonl(eval_dir: Path) -> None:
    questions = load_questions(eval_dir / "questions.jsonl")
    assert [q.id for q in questions] == ["q-single", "q-multi"]
    assert questions[1].kind == "multi_hop"
    assert questions[1].gold_docs == ["doc-01", "doc-02", "doc-03"]


def test_evaluate_reports_one_row_per_config(index, eval_dir: Path) -> None:
    questions = load_questions(eval_dir / "questions.jsonl")
    report = evaluate(
        index,
        questions,
        top_k=6,
        configs=[("lexical", ("lexical",), {}), ("hybrid", ALL_CHANNELS, {})],
    )
    assert [config.name for config in report.configs] == ["lexical", "hybrid"]
    assert report.questions == 2
    markdown = report.to_markdown()
    assert markdown.count("|") >= 3 * 3
    assert "hybrid" in markdown


def test_metrics_match_manual_computation(index, eval_dir: Path) -> None:
    questions = load_questions(eval_dir / "questions.jsonl")
    report = evaluate(index, questions, top_k=6, configs=[("hybrid", ALL_CHANNELS, {})])
    per_question = report.configs[0].per_question
    manual_recall = sum(float(row["recall"]) for row in per_question) / len(per_question)
    assert abs(report.configs[0].recall_at_k - manual_recall) < 1e-9
    assert all(0.0 <= float(row["recall"]) <= 1.0 for row in per_question)
    assert all(float(row["reciprocal_rank"]) <= 1.0 for row in per_question)
    assert all(0.0 <= float(row["keyword_recall"]) <= 1.0 for row in per_question)
    assert "keywords_present" in per_question[0]


def test_keyword_recall_detects_missing_evidence(index, eval_dir: Path) -> None:
    questions = load_questions(eval_dir / "questions.jsonl")
    report = evaluate(index, questions, top_k=6, configs=[("hybrid", ALL_CHANNELS, {})])
    rows = {row["id"]: row for row in report.configs[0].per_question}
    # 单跳问题（发布窗口）在 top-6 上下文里必须能找到「周三 / 21:00」
    assert rows["q-single"]["keyword_recall"] == 1.0


def test_multi_hop_split_is_reported_separately(index, eval_dir: Path) -> None:
    questions = load_questions(eval_dir / "questions.jsonl")
    report = evaluate(index, questions, top_k=6, configs=[("hybrid", ALL_CHANNELS, {})])
    config = report.configs[0]
    assert 0.0 <= config.recall_single <= 1.0
    assert 0.0 <= config.recall_multi <= 1.0
    # 单跳问题必须能被检索覆盖，否则说明索引本身有问题
    assert config.recall_single == 1.0


def test_graph_channel_improves_multi_hop_recall(index, eval_dir: Path) -> None:
    questions = load_questions(eval_dir / "questions.jsonl")
    report = evaluate(
        index,
        questions,
        top_k=3,
        configs=[("lexical", ("lexical",), {}), ("hybrid", ALL_CHANNELS, {})],
    )
    lexical, hybrid = report.configs
    assert hybrid.recall_multi >= lexical.recall_multi
    assert hybrid.recall_at_k >= lexical.recall_at_k


def test_report_serializes_for_evidence(index, eval_dir: Path) -> None:
    import json

    questions = load_questions(eval_dir / "questions.jsonl")
    report = evaluate(index, questions, top_k=6, configs=[("lexical", ("lexical",), {})])
    payload = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
    assert payload["configs"][0]["per_question"][0]["retrieved_docs"]


def test_score_answer_counts_keywords_and_citations() -> None:
    record = QuestionRecord(
        id="q1",
        question="谁审批",
        kind="multi_hop",
        gold_docs=["doc-01", "doc-02"],
        answer_keywords=["张岚", "安全合规部"],
    )
    score = score_answer(record, "由张岚签字 [1]，归属安全合规部 [2]。", ["doc-02", "doc-03", "doc-01"])
    assert score.keyword_coverage == 1.0
    # 引用里只有 doc-01/doc-02 属于 gold，doc-03 不计入召回
    assert score.citation_recall == 1.0
    assert score.to_dict()["citations"] == ["doc-02", "doc-03", "doc-01"]


def test_score_answer_penalizes_missing_keywords() -> None:
    record = QuestionRecord(
        id="q1",
        question="谁审批",
        kind="single_hop",
        gold_docs=["doc-01"],
        answer_keywords=["张岚", "安全合规部"],
    )
    score = score_answer(record, "资料不足。", [])
    assert score.keyword_coverage == 0.0
    assert score.citation_recall == 0.0
