"""融合层：RRF 的排序语义与可审计性。"""

from __future__ import annotations

from graphrag_mvp.fusion import RRF_K, reciprocal_rank_fusion, single_channel
from graphrag_mvp.types import RankedHit


def hits(channel: str, *chunk_ids: str) -> list[RankedHit]:
    return [
        RankedHit(chunk_id=chunk_id, score=1.0 / rank, rank=rank, channel=channel)
        for rank, chunk_id in enumerate(chunk_ids, start=1)
    ]


def test_single_channel_order_preserved() -> None:
    fused = reciprocal_rank_fusion({"lexical": hits("lexical", "a", "b", "c")})
    assert [hit.chunk_id for hit in fused] == ["a", "b", "c"]
    assert [hit.rank for hit in fused] == [1, 2, 3]


def test_consensus_across_channels_wins() -> None:
    # b 在两个通道都排第 1，a/c 各只在一个通道靠前 → b 融合后第 1
    fused = reciprocal_rank_fusion({"lexical": hits("lexical", "a", "b"), "dense": hits("dense", "b", "c")})
    assert fused[0].chunk_id == "b"
    assert fused[0].channels == {"lexical": 2, "dense": 1}


def test_weights_shift_the_ranking() -> None:
    # 两条通道各命中不同的 chunk：词法重则 a 第一，图谱重则 c 第一
    channel_hits = {"lexical": hits("lexical", "a", "b"), "graph": hits("graph", "c", "d")}
    lexical_heavy = reciprocal_rank_fusion(channel_hits, {"lexical": 5.0, "graph": 0.1})
    graph_heavy = reciprocal_rank_fusion(channel_hits, {"lexical": 0.1, "graph": 5.0})
    assert lexical_heavy[0].chunk_id == "a"
    assert graph_heavy[0].chunk_id == "c"


def test_zero_weight_disables_a_channel_but_keeps_provenance_clean() -> None:
    fused = reciprocal_rank_fusion(
        {"lexical": hits("lexical", "a"), "dense": hits("dense", "b")}, {"dense": 0.0}
    )
    assert [hit.chunk_id for hit in fused] == ["a"]
    assert fused[0].channels == {"lexical": 1}


def test_rrf_score_matches_formula() -> None:
    fused = reciprocal_rank_fusion({"lexical": hits("lexical", "a", "b")}, {"lexical": 2.0})
    assert fused[0].score == round(2.0 / (RRF_K + 1), 8)
    assert fused[1].score == round(2.0 / (RRF_K + 2), 8)


def test_top_k_truncates_but_keeps_ranks() -> None:
    fused = reciprocal_rank_fusion({"lexical": hits("lexical", "a", "b", "c")}, top_k=2)
    assert [hit.chunk_id for hit in fused] == ["a", "b"]


def test_chunks_missing_from_every_channel_do_not_appear() -> None:
    fused = reciprocal_rank_fusion({"lexical": hits("lexical", "a")})
    assert "b" not in {hit.chunk_id for hit in fused}


def test_single_channel_helper_filters_by_channel() -> None:
    combined = [*hits("lexical", "a"), *hits("dense", "b")]
    fused = single_channel(combined, "dense")
    assert [hit.chunk_id for hit in fused] == ["b"]
    assert fused[0].channels == {"dense": 1}


# ---- 通道保底（channel_floor）------------------------------------------------
def test_channel_floor_reserves_slots_for_quiet_channels() -> None:
    # graph 通道的最强命中（z）在全局只排第 3，保底后必须进入最终列表
    channel_hits = {
        "lexical": hits("lexical", "a", "b", "c"),
        "graph": hits("graph", "z"),
    }
    plain = reciprocal_rank_fusion(channel_hits, {"lexical": 1.0, "graph": 0.05}, top_k=4)
    floored = reciprocal_rank_fusion(channel_hits, {"lexical": 1.0, "graph": 0.05}, top_k=4, channel_floor=1)
    assert "z" not in {hit.chunk_id for hit in plain[:3]}
    assert "z" in {hit.chunk_id for hit in floored}


def test_channel_floor_keeps_rrf_head_order() -> None:
    channel_hits = {"lexical": hits("lexical", "a", "b", "c"), "graph": hits("graph", "z")}
    fused = reciprocal_rank_fusion(channel_hits, {"lexical": 1.0, "graph": 0.1}, top_k=4, channel_floor=1)
    assert [hit.chunk_id for hit in fused] == ["a", "b", "c", "z"], "保底只占用尾部席位，不扰动 RRF 头部"


def test_channel_floor_never_exceeds_top_k() -> None:
    channel_hits = {
        "lexical": hits("lexical", "a", "b"),
        "dense": hits("dense", "c", "d"),
        "graph": hits("graph", "e", "f"),
        "community": hits("community", "g"),
    }
    fused = reciprocal_rank_fusion(channel_hits, top_k=2, channel_floor=5)
    assert len(fused) == 2
    assert [hit.rank for hit in fused] == [1, 2]


def test_channel_floor_zero_is_the_plain_rrf_behaviour() -> None:
    channel_hits = {"lexical": hits("lexical", "a", "b"), "graph": hits("graph", "c")}
    # 同分时按 chunk_id 稳定排序（c 与 a 同为 1/61，a 在前）
    assert [hit.chunk_id for hit in reciprocal_rank_fusion(channel_hits, top_k=3, channel_floor=0)] == [
        "a",
        "c",
        "b",
    ]
    # 保底打开后：头部保留 1 席（top_k=3 − 2 通道×1），两通道各补 1 条未被选中的命中
    floored = reciprocal_rank_fusion(channel_hits, top_k=3, channel_floor=1)
    assert [hit.chunk_id for hit in floored] == ["a", "b", "c"]


def test_empty_channel_does_not_take_a_reserved_slot() -> None:
    channel_hits = {"lexical": hits("lexical", "a", "b"), "graph": []}
    fused = reciprocal_rank_fusion(channel_hits, top_k=2, channel_floor=1)
    assert [hit.chunk_id for hit in fused] == ["a", "b"]
