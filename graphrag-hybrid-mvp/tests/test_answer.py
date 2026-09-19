"""回答层：证据编号、引用抽取与反幻觉约束。"""

from __future__ import annotations

from graphrag_mvp.answer import (
    ANSWER_SYSTEM,
    answer_question,
    build_answer_messages,
    extract_citations,
    format_evidence,
    summarize_communities_for_display,
)
from graphrag_mvp.llm import ScriptedLLM
from graphrag_mvp.types import Chunk, Community, FusedHit, RetrievalResult


def _chunk(chunk_id: str, doc_id: str, title: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id, doc_id=doc_id, doc_title=title, section="小节", text=f"{title} 的正文", ordinal=1
    )


def _retrieval() -> RetrievalResult:
    chunks = [_chunk("doc-01#1", "doc-01", "甲文档"), _chunk("doc-02#1", "doc-02", "乙文档")]
    return RetrievalResult(
        question="谁审批？",
        hits=[FusedHit(chunk_id="doc-01#1", score=0.1, rank=1, channels={"lexical": 1})],
        chunks=chunks,
        communities=[
            Community(community_id="c01", entities=["CR-2"], chunk_ids=["doc-01#1"], summary="审批链摘要")
        ],
        linked_entities=["CR-2"],
        per_channel={},
        weights={"lexical": 1.0},
        channels=["lexical"],
    )


def test_prompt_numbers_evidence_blocks() -> None:
    messages = build_answer_messages("谁审批？", _retrieval())
    user = messages[1]["content"]
    assert "[1] 来源 doc-01#1" in user
    assert "[2] 来源 doc-02#1" in user
    assert "问题：谁审批？" in user


def test_prompt_includes_community_summary_as_background() -> None:
    user = build_answer_messages("谁审批？", _retrieval())[1]["content"]
    assert "审批链摘要" in user


def test_system_prompt_forbids_outside_knowledge() -> None:
    assert "资料不足" in ANSWER_SYSTEM
    assert "禁止" in ANSWER_SYSTEM


def test_extract_citations_maps_index_to_doc_id() -> None:
    assert extract_citations("结论 [2]。", _retrieval()) == ["doc-02"]


def test_extract_citations_dedupes_and_ignores_out_of_range() -> None:
    assert extract_citations("[1][1][9]", _retrieval()) == ["doc-01"]


def test_extract_citations_picks_explicit_doc_ids() -> None:
    assert extract_citations("见 doc-02 的规定", _retrieval()) == ["doc-02"]


def test_extract_citations_ignores_doc_ids_outside_context() -> None:
    assert extract_citations("见 doc-99 的规定", _retrieval()) == []


def test_answer_question_returns_usage_and_citations() -> None:
    llm = ScriptedLLM(default="由安全审批人签字 [1]。")
    answer = answer_question("谁审批？", _retrieval(), llm)
    assert answer.text == "由安全审批人签字 [1]。"
    assert answer.citations == ["doc-01"]
    assert answer.usage == {"prompt_tokens": 0, "completion_tokens": 0}
    assert answer.to_dict()["retrieval"]["chunks"]


def test_format_evidence_shows_channels() -> None:
    text = format_evidence(_retrieval())
    assert "doc-01#1" in text
    assert "lexical#1" in text
    assert "CR-2" in text


def test_summarize_communities_for_display_renders_summary() -> None:
    text = summarize_communities_for_display(_retrieval().communities)
    assert "c01" in text and "审批链摘要" in text
