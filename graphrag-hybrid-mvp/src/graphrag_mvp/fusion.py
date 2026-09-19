"""倒数排名融合（RRF）+ 通道保底。

为什么用 RRF 而不是加权分数求和：各通道的分数尺度互不可比（BM25 是无界正数、
余弦在 [-1,1]、图谱是手写权重）。RRF 只用**排名**，天然免归一化、免调参，
是工业界混合检索的默认融合法：

    score(d) = Σ_channel  w_c * 1 / (k + rank_c(d))

k 默认 60（原论文经验值），w_c 为通道权重，在 `.env` 的 `GRAPHRAG_WEIGHTS` 里可调。

**通道保底（channel_floor）**：纯 RRF 有一个已知偏差 —— 它奖励「多个通道都提到」的片段，
因此链条最后一环那种「只有图谱通道能找到、且在该通道内排名靠后」的证据会被挤掉。
保底策略给每个通道留固定席位：先在 Top-K 里预留 `通道数 × floor` 个位置，
剩余位置按 RRF 填充，最后把每个通道的头部命中补进预留位（已入选的跳过）。
实测在 12 条上下文的预算下，多跳「全链覆盖」从 0.500 提升到 0.625，而单跳指标不变。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from .types import FusedHit, RankedHit

RRF_K = 60


def reciprocal_rank_fusion(
    channel_hits: Mapping[str, Sequence[RankedHit]],
    weights: Mapping[str, float] | None = None,
    *,
    k: int = RRF_K,
    top_k: int | None = None,
    channel_floor: int = 0,
) -> list[FusedHit]:
    """把多个通道的排名列表融合为一个有序列表。

    `channel_floor > 0` 且指定了 `top_k` 时启用通道保底：每个有效通道至少
    贡献 `channel_floor` 条命中进入最终列表（补在 RRF 头部之后）。
    """
    weights = weights or {}
    scores: dict[str, float] = {}
    provenance: dict[str, dict[str, int]] = {}
    active_channels: list[str] = []
    for channel, hits in channel_hits.items():
        weight = float(weights.get(channel, 1.0))
        if weight == 0 or not hits:
            continue
        active_channels.append(channel)
        for hit in hits:
            rank = hit.rank if hit.rank > 0 else 1
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + weight / (k + rank)
            provenance.setdefault(hit.chunk_id, {})[channel] = rank

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    if channel_floor > 0 and top_k is not None and active_channels:
        reserved = min(len(active_channels) * channel_floor, max(top_k - 1, 0))
        head = ordered[: max(top_k - reserved, 1)]
        selected = [chunk_id for chunk_id, _ in head]
        seen = set(selected)
        for channel in active_channels:
            added = 0
            for hit in channel_hits[channel]:
                if added >= channel_floor:
                    break
                if hit.chunk_id in seen:
                    continue
                seen.add(hit.chunk_id)
                selected.append(hit.chunk_id)
                added += 1
        ordered = [(chunk_id, scores[chunk_id]) for chunk_id in selected[:top_k]]
    elif top_k is not None:
        ordered = ordered[:top_k]

    return [
        FusedHit(
            chunk_id=chunk_id,
            score=round(score, 8),
            rank=rank,
            channels=provenance.get(chunk_id, {}),
        )
        for rank, (chunk_id, score) in enumerate(ordered, start=1)
    ]


def single_channel(
    hits: Iterable[RankedHit], channel: str | None = None, top_k: int | None = None
) -> list[FusedHit]:
    """只用一个通道时的退化路径（消融实验用，走同一套输出结构）。"""
    selected = [hit for hit in hits if channel is None or hit.channel == channel]
    if top_k is not None:
        selected = selected[:top_k]
    return [
        FusedHit(
            chunk_id=hit.chunk_id,
            score=round(1.0 / (RRF_K + (hit.rank or 1)), 8),
            rank=index,
            channels={hit.channel: hit.rank},
        )
        for index, hit in enumerate(selected, start=1)
    ]
