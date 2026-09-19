"""社区发现：把大规模图压成「话题簇」，让检索可以宏观回答。

GraphRAG 相较朴素 RAG 的关键增项：局部检索（chunk 级）只能回答「某条款说什么」，
而「跨多个系统的整体要求是什么」这类问题需要先在图上划出话题簇、再由 LLM 生成簇摘要，
检索时把簇摘要一起喂给模型。

算法用标签传播（label propagation）：无需调参、无需向量化、线性时间，
在几百节点的企业知识图谱上足够；实现保持**确定性**（按度数排序、平票取字典序最小标签），
否则每次构建索引都会得到不同社区，无法复现实验。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .graph import KnowledgeGraph
from .types import Community


def label_propagation(graph: KnowledgeGraph, *, max_iter: int = 50, min_size: int = 2) -> list[list[str]]:
    """返回社区列表（每个社区是实体名列表）。孤立节点与过小社区会并入单例。"""
    labels: dict[str, str] = {name: name for name in graph.entities}
    # 度数降序 → 名字升序：先处理枢纽节点，结果稳定
    order = sorted(graph.entities, key=lambda name: (-len(graph.adjacency.get(name, set())), name))
    for _ in range(max_iter):
        changed = False
        for node in order:
            neighbors = graph.adjacency.get(node, set())
            if not neighbors:
                continue
            votes = Counter(labels[neighbor] for neighbor in neighbors)
            # 平票：取字典序最小标签，保证确定性
            best = min(votes.items(), key=lambda item: (-item[1], item[0]))[0]
            if labels[node] != best:
                labels[node] = best
                changed = True
        if not changed:
            break

    groups: dict[str, list[str]] = {}
    for node, label in labels.items():
        groups.setdefault(label, []).append(node)

    communities = [sorted(members) for members in groups.values() if len(members) >= min_size]
    communities.sort(key=lambda members: (-len(members), members[0]))
    return communities


def build_communities(
    graph: KnowledgeGraph,
    *,
    min_size: int = 2,
    max_chunks_per_community: int = 24,
) -> list[Community]:
    """社区结构体：成员实体 + 关联 chunk（按被提及次数排序，作为摘要与检索的证据面）。"""
    communities: list[Community] = []
    for index, members in enumerate(label_propagation(graph, min_size=min_size), start=1):
        chunk_weights: Counter[str] = Counter()
        for entity in members:
            for chunk_id in graph.entities[entity].chunk_ids:
                chunk_weights[chunk_id] += 1
        ordered_chunks = [chunk_id for chunk_id, _ in chunk_weights.most_common(max_chunks_per_community)]
        communities.append(
            Community(
                community_id=f"c{index:02d}",
                entities=members,
                chunk_ids=ordered_chunks,
            )
        )
    return communities


def save_communities(communities: list[Community], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([community.to_dict() for community in communities], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_communities(path: Path) -> list[Community]:
    if not path.is_file():
        return []
    return [Community.from_dict(raw) for raw in json.loads(path.read_text(encoding="utf-8"))]
