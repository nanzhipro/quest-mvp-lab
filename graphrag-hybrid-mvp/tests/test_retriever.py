"""混合检索器：通道开关、图谱多跳召回与来源可审计。"""

from __future__ import annotations

from graphrag_mvp.config import DEFAULT_WEIGHTS
from graphrag_mvp.retriever import ALL_CHANNELS, keyword_overlap, retrieve

MULTI_HOP_QUESTION = "Nebula 高风险变更最后拍板的人是谁？"


def test_all_channels_are_exercised(index) -> None:
    result = retrieve(index, MULTI_HOP_QUESTION, top_k=6, weights=DEFAULT_WEIGHTS)
    assert set(result.per_channel) <= set(ALL_CHANNELS)
    assert {"lexical", "dense", "graph"} <= set(result.per_channel)
    assert result.chunks, "融合后应有上下文"


def test_graph_channel_retrieves_the_whole_multi_hop_chain(index) -> None:
    result = retrieve(index, MULTI_HOP_QUESTION, top_k=6, channels=["graph"])
    graph_docs = {index.chunk(hit.chunk_id).doc_id for hit in result.hits}
    assert graph_docs == {"doc-01", "doc-02", "doc-03"}, "图谱通道应覆盖完整的三跳链条"


def test_single_channels_cannot_cover_the_chain_in_a_tight_context(index) -> None:
    """上下文预算只有 3 条时，任一单通道都会漏掉链条上的一环。"""
    gold = {"doc-01", "doc-02", "doc-03"}
    for channel in ("lexical", "dense", "graph"):
        result = retrieve(index, MULTI_HOP_QUESTION, top_k=3, channels=[channel])
        docs = {index.chunk(hit.chunk_id).doc_id for hit in result.hits}
        assert docs != gold, f"{channel} 单通道不应在 top-3 内覆盖全部三跳"


def test_hybrid_fits_the_whole_chain_into_a_tighter_context(index) -> None:
    lexical = retrieve(index, MULTI_HOP_QUESTION, top_k=3, channels=["lexical"])
    hybrid = retrieve(
        index, MULTI_HOP_QUESTION, top_k=3, channels=list(ALL_CHANNELS), weights=DEFAULT_WEIGHTS
    )
    lexical_docs = {index.chunk(hit.chunk_id).doc_id for hit in lexical.hits}
    hybrid_docs = {index.chunk(hit.chunk_id).doc_id for hit in hybrid.hits}
    assert {"doc-01", "doc-02", "doc-03"} <= hybrid_docs
    assert len(hybrid_docs) > len(lexical_docs)


def test_entities_are_linked_from_the_question(index) -> None:
    result = retrieve(index, MULTI_HOP_QUESTION, top_k=6, weights=DEFAULT_WEIGHTS)
    assert "Nebula" in result.linked_entities


def test_community_context_is_attached_when_entities_link(index) -> None:
    result = retrieve(index, MULTI_HOP_QUESTION, top_k=6, channels=list(ALL_CHANNELS))
    assert result.communities, "链接到实体后应带出相关话题簇摘要"
    assert all(community.summary for community in result.communities)


def test_disabling_a_channel_removes_its_provenance(index) -> None:
    result = retrieve(index, MULTI_HOP_QUESTION, top_k=6, channels=["lexical", "dense"])
    channels_used = {name for hit in result.hits for name in hit.channels}
    assert "graph" not in channels_used
    assert "graph" not in result.per_channel


def test_weights_can_demote_a_channel(index) -> None:
    lexical_first = retrieve(
        index,
        MULTI_HOP_QUESTION,
        top_k=6,
        channels=["lexical", "graph"],
        weights={"lexical": 10.0, "graph": 0.01},
    )
    graph_first = retrieve(
        index,
        MULTI_HOP_QUESTION,
        top_k=6,
        channels=["lexical", "graph"],
        weights={"lexical": 0.01, "graph": 10.0},
    )
    assert [hit.chunk_id for hit in lexical_first.hits] != [hit.chunk_id for hit in graph_first.hits]
    assert lexical_first.weights == {"lexical": 10.0, "graph": 0.01}


def test_weights_are_echoed_for_auditing(index) -> None:
    result = retrieve(index, "问题", top_k=3, channels=["lexical"], weights=DEFAULT_WEIGHTS)
    assert result.weights == {"lexical": 1.0}


def test_unknown_channels_are_ignored_not_crashed(index) -> None:
    result = retrieve(index, MULTI_HOP_QUESTION, top_k=3, channels=["lexical", "nope"])
    assert result.channels == ["lexical"]


def test_retrieval_result_serializes(index) -> None:
    result = retrieve(index, MULTI_HOP_QUESTION, top_k=3, weights=DEFAULT_WEIGHTS)
    payload = result.to_dict()
    assert payload["question"].startswith("Nebula")
    assert payload["chunks"] and payload["hits"][0]["channels"]


def test_keyword_overlap_is_zero_for_disjoint_text() -> None:
    assert keyword_overlap("Nebula", "完全无关内容") < 0.2
    assert keyword_overlap("发布窗口", "发布窗口固定为每周三") > 0.0
