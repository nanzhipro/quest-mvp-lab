"""社区发现：确定性、簇划分与 chunk 关联。"""

from __future__ import annotations

from graphrag_mvp.communities import (
    build_communities,
    label_propagation,
    load_communities,
    save_communities,
)
from graphrag_mvp.graph import Extraction, KnowledgeGraph


def _graph_from_edges(edges: list[tuple[str, str]]) -> KnowledgeGraph:
    extraction = Extraction(
        chunk_id="doc-01#1",
        entities=[{"name": node, "type": "系统"} for node in {n for edge in edges for n in edge}],
        relations=[{"source": a, "target": b, "predicate": "关联"} for a, b in edges],
    )
    return KnowledgeGraph.from_extractions([extraction])


def test_label_propagation_is_deterministic() -> None:
    graph = _graph_from_edges([("A", "B"), ("B", "C"), ("C", "A"), ("D", "E"), ("E", "F"), ("F", "D")])
    first = label_propagation(graph)
    second = label_propagation(graph)
    assert first == second


def test_label_propagation_splits_two_clusters() -> None:
    graph = _graph_from_edges([("A", "B"), ("B", "C"), ("C", "A"), ("D", "E"), ("E", "F"), ("F", "D")])
    communities = label_propagation(graph)
    assert len(communities) == 2
    assert ["A", "B", "C"] in communities and ["D", "E", "F"] in communities


def test_singletons_are_filtered_by_min_size() -> None:
    graph = _graph_from_edges([("A", "B")])
    graph.entities["孤立节点"] = graph.entities["A"].__class__(name="孤立节点", type="系统")
    graph.adjacency["孤立节点"] = set()
    assert label_propagation(graph, min_size=2) == [["A", "B"]]


def test_build_communities_orders_chunks_by_mention_count(graph: KnowledgeGraph) -> None:
    communities = build_communities(graph, min_size=2)
    assert communities
    first = communities[0]
    assert first.community_id == "c01"
    assert first.chunk_ids, "社区应带上代表性 chunk"
    assert len(set(first.chunk_ids)) == len(first.chunk_ids), "chunk 不应重复"


def test_communities_roundtrip(tmp_path, graph: KnowledgeGraph) -> None:
    communities = build_communities(graph)
    path = tmp_path / "communities.json"
    save_communities(communities, path)
    reloaded = load_communities(path)
    assert [c.community_id for c in reloaded] == [c.community_id for c in communities]
    assert reloaded[0].entities == communities[0].entities


def test_load_communities_returns_empty_when_missing(tmp_path) -> None:
    assert load_communities(tmp_path / "nope.json") == []
