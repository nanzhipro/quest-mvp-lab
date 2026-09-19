"""混合检索器：四个通道 + RRF 融合，并保留每个命中的来源证据。

    问题 ──┬─ lexical : BM25（术语、编号、字面命中）
           ├─ dense   : 中文向量余弦（同义改写、口语提问）
           ├─ graph   : 实体链接 → 图上 1~2 跳展开 → 回到出处 chunk（多跳、跨文档）
           └─ community: 社区摘要（宏观、跨系统整体要求）
                  │
                  └─ RRF 融合（加权）→ Top-K chunk + 相关社区摘要

设计要点：
1. 通道开关与权重由参数控制，消融实验只是「关掉某些通道」，不需要另写代码路径。
2. 图谱通道的实体链接是**确定性**的（字面命中 + 令牌重合兜底），不调用 LLM，
   这样检索评测可复现、可离线跑；实体抽取才用 LLM，且只在建索引时跑一次。
3. 每个 FusedHit 都带 channels 字段（通道 → 该通道内排名），
   回答可以解释「这条依据是被哪条通道捞出来的」，便于定位召回问题。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .embedding import DenseIndex, Embedder
from .fusion import reciprocal_rank_fusion
from .graph import KnowledgeGraph, link_entities_by_lexical
from .lexical import BM25Index, tokenize
from .types import Chunk, Community, RankedHit, RetrievalResult

ALL_CHANNELS = ("lexical", "dense", "graph", "community")


@dataclass
class RetrieverIndex:
    """已构建好的索引集合（一次装载，多次查询）。"""

    chunks: list[Chunk]
    bm25: BM25Index
    dense: DenseIndex | None = None
    graph: KnowledgeGraph | None = None
    communities: list[Community] = field(default_factory=list)
    embedder: Embedder | None = None
    _by_id: dict[str, Chunk] = field(default_factory=dict, init=False, repr=False)
    _community_by_entity: dict[str, list[Community]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {chunk.chunk_id: chunk for chunk in self.chunks}
        for community in self.communities:
            for entity in community.entities:
                self._community_by_entity.setdefault(entity, []).append(community)

    def chunk(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)


def _seed_entities(
    index: RetrieverIndex,
    question: str,
    per_channel: dict[str, list[RankedHit]],
    *,
    max_seeds: int = 12,
    chunk_seeds: int = 3,
    entities_per_chunk: int = 4,
) -> tuple[list[str], set[str]]:
    """实体链接：把问题映射到图上的种子节点。

    三段式（越靠前权重越高）：
    1. 问题里**字面出现**的实体名/别名（最可信）；
    2. 令牌重合兜底（问题没写全称，但提到了关键词）；
    3. 已召回片段提到的实体 —— 多跳问题的最后一环常常在问题字面上完全不出现，
       只能从「链的前一环」所在片段出发沿图走过去。
    返回 (种子列表, 问题直连的种子集合)。
    """
    if index.graph is None:
        return [], set()
    graph = index.graph
    question_seeds: list[str] = graph.search_names(question)
    if len(question_seeds) < 2:
        for name in link_entities_by_lexical(question, graph, top_k=3):
            if name not in question_seeds:
                question_seeds.append(name)

    seeds = list(question_seeds)
    if len(seeds) < 3:
        hits = per_channel.get("lexical") or per_channel.get("dense") or index.bm25.search(question, top_k=3)
        for hit in hits[:chunk_seeds]:
            for entity in sorted(graph.chunk_entities.get(hit.chunk_id, set()))[:entities_per_chunk]:
                if entity not in seeds:
                    seeds.append(entity)
    return seeds[:max_seeds], set(question_seeds)


def _graph_ranked_chunks(
    graph: KnowledgeGraph,
    distance: dict[str, int],
    question_seeds: set[str],
    *,
    bucket_quota: int,
    max_total: int,
) -> list[tuple[str, float, int]]:
    """按「距种子跳数」分桶排序，再逐桶取定额。

    为什么分桶而不是全局按分数排：多跳链的后几环天然离种子更远，权重更小，
    若全局排序会被大量「近但无关」的片段挤掉。分桶等于给远跳留固定席位，
    保证链条能整条进入上下文 —— 这正是 GraphRAG 相对朴素 RAG 的收益来源。

    返回 (chunk_id, 分数, 最小跳数) 列表，按跳数升序、桶内分数降序。
    """
    per_chunk_depth: dict[str, int] = {}
    per_chunk_score: dict[str, float] = {}
    for entity, depth in distance.items():
        entity_obj = graph.entities.get(entity)
        if entity_obj is None:
            continue
        decay = 0.5**depth
        base = 1.0 if entity in question_seeds else 0.5
        for chunk_id in entity_obj.chunk_ids:
            if chunk_id not in per_chunk_depth or depth < per_chunk_depth[chunk_id]:
                per_chunk_depth[chunk_id] = depth
            per_chunk_score[chunk_id] = per_chunk_score.get(chunk_id, 0.0) + base * decay

    buckets: dict[int, list[str]] = {}
    for chunk_id, depth in per_chunk_depth.items():
        buckets.setdefault(depth, []).append(chunk_id)

    ranked: list[tuple[str, float, int]] = []
    for depth in sorted(buckets):
        members = sorted(buckets[depth], key=lambda cid: (-per_chunk_score[cid], cid))
        for chunk_id in members[:bucket_quota]:
            ranked.append((chunk_id, round(per_chunk_score[chunk_id], 6), depth))
        if len(ranked) >= max_total:
            break
    return ranked[:max_total]


def retrieve(
    index: RetrieverIndex,
    question: str,
    *,
    top_k: int = 8,
    channels: Sequence[str] = ALL_CHANNELS,
    weights: dict[str, float] | None = None,
    per_channel_k: int = 20,
    graph_hops: int = 2,
    max_community_context: int = 3,
    channel_floor: int = 0,
) -> RetrievalResult:
    """执行多通道召回并融合。`channels` 为空时退化为词法单通道。"""
    enabled = [name for name in channels if name in ALL_CHANNELS]
    per_channel: dict[str, list[RankedHit]] = {}
    linked_entities: list[str] = []

    if "lexical" in enabled:
        per_channel["lexical"] = index.bm25.search(question, top_k=per_channel_k)

    if "dense" in enabled and index.dense is not None and index.embedder is not None:
        query_vector = index.embedder.embed_query(question)
        per_channel["dense"] = index.dense.search(query_vector, top_k=per_channel_k)

    community_hits: list[Community] = []
    if ("graph" in enabled or "community" in enabled) and index.graph is not None:
        seeds, question_seeds = _seed_entities(index, question, per_channel)
        linked_entities = seeds
        if "graph" in enabled and seeds:
            distance = index.graph.expand(seeds, hops=graph_hops)
            # 分桶排序：近跳优先，但每跳都留固定席位，避免远跳被近跳淹没
            ranked = _graph_ranked_chunks(
                index.graph,
                distance,
                question_seeds,
                bucket_quota=max(4, per_channel_k // max(1, graph_hops + 1)),
                max_total=per_channel_k,
            )
            per_channel["graph"] = [
                RankedHit(chunk_id=chunk_id, score=score, rank=rank, channel="graph")
                for rank, (chunk_id, score, _depth) in enumerate(ranked, start=1)
            ]
        if "community" in enabled and seeds:
            seen: set[str] = set()
            for seed in seeds[:3]:
                for community in index._community_by_entity.get(seed, []):
                    if community.community_id not in seen:
                        seen.add(community.community_id)
                        community_hits.append(community)
            community_hits = community_hits[:max_community_context]
            # 社区通道的 chunk 命中：社区摘要所覆盖的代表性 chunk
            community_chunks: dict[str, float] = {}
            for community in community_hits:
                for position, chunk_id in enumerate(community.chunk_ids[:12], start=1):
                    community_chunks[chunk_id] = max(community_chunks.get(chunk_id, 0.0), 1.0 / position)
            ordered_comm = sorted(community_chunks.items(), key=lambda item: (-item[1], item[0]))[
                :per_channel_k
            ]
            if ordered_comm:
                per_channel["community"] = [
                    RankedHit(chunk_id=chunk_id, score=round(score, 6), rank=rank, channel="community")
                    for rank, (chunk_id, score) in enumerate(ordered_comm, start=1)
                ]

    fused = reciprocal_rank_fusion(per_channel, weights or {}, top_k=top_k, channel_floor=channel_floor)
    chunks = [chunk for chunk in (index.chunk(hit.chunk_id) for hit in fused) if chunk is not None]
    return RetrievalResult(
        question=question,
        hits=fused,
        chunks=chunks,
        communities=community_hits,
        linked_entities=linked_entities,
        per_channel=per_channel,
        weights={name: float((weights or {}).get(name, 1.0)) for name in enabled},
        channels=enabled,
    )


def keyword_overlap(query: str, text: str) -> float:
    """调试用：查询与文本的令牌重合率（Jaccard）。"""
    left, right = set(tokenize(query)), set(tokenize(text))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
