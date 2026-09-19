"""社区摘要：缓存复用与失败降级。"""

from __future__ import annotations

from graphrag_mvp.llm import ScriptedLLM
from graphrag_mvp.summarize import SummaryCache, summarize_communities
from graphrag_mvp.types import Chunk, Community


def _chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=chunk_id.split("#")[0],
        doc_title="标题",
        section="小节",
        text=text,
        ordinal=1,
    )


CHUNKS = {"doc-01#1": _chunk("doc-01#1", "Nebula 高风险变更走 CR-2。")}


def _community() -> Community:
    return Community(community_id="c01", entities=["Nebula", "CR-2"], chunk_ids=["doc-01#1"])


def test_summary_is_filled_from_model_output(tmp_path) -> None:
    llm = ScriptedLLM(default='{"summary": "涉及 Nebula 的变更审批链。"}')
    communities = summarize_communities([_community()], CHUNKS, llm, cache=SummaryCache(tmp_path / "s.jsonl"))
    assert communities[0].summary == "涉及 Nebula 的变更审批链。"


def test_summary_is_reused_from_cache(tmp_path) -> None:
    path = tmp_path / "s.jsonl"
    llm = ScriptedLLM(default='{"summary": "缓存内容"}')
    summarize_communities([_community()], CHUNKS, llm, cache=SummaryCache(path))
    calls_after_first = len(llm.calls)
    again = summarize_communities([_community()], CHUNKS, llm, cache=SummaryCache(path))
    assert again[0].summary == "缓存内容"
    assert len(llm.calls) == calls_after_first, "第二次应命中磁盘缓存，不再调用模型"


def test_broken_json_falls_back_to_entity_list(tmp_path) -> None:
    llm = ScriptedLLM(default="这不是 JSON")
    communities = summarize_communities([_community()], CHUNKS, llm, cache=SummaryCache(tmp_path / "s.jsonl"))
    assert "Nebula" in communities[0].summary


def test_empty_summary_falls_back_instead_of_blank(tmp_path) -> None:
    llm = ScriptedLLM(default='{"summary": ""}')
    communities = summarize_communities([_community()], CHUNKS, llm, cache=SummaryCache(tmp_path / "s.jsonl"))
    assert communities[0].summary.strip()


def test_prompt_includes_entity_list_and_chunk_text(tmp_path) -> None:
    llm = ScriptedLLM(default='{"summary": "x"}')
    summarize_communities([_community()], CHUNKS, llm, cache=SummaryCache(tmp_path / "s.jsonl"))
    prompt = llm.calls[0]["prompt"]
    assert "Nebula" in prompt and "CR-2" in prompt
    assert "doc-01#1" in prompt
