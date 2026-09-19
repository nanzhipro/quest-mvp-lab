"""图谱通道：抽取解析、实体归并、多跳展开与持久化。"""

from __future__ import annotations

import json
from pathlib import Path

from graphrag_mvp.graph import (
    Extraction,
    KnowledgeGraph,
    link_entities_by_lexical,
    normalize_name,
    parse_extraction,
)
from graphrag_mvp.llm import ScriptedLLM


def test_normalize_name_folds_fullwidth_and_whitespace() -> None:
    assert normalize_name("Ｎｅｂｕｌａ　平台") == "Nebula平台"
    assert normalize_name("《CR-2》") == "CR-2"
    assert normalize_name(" 张岚 的") == "张岚"


def test_parse_extraction_handles_markdown_fence() -> None:
    raw = '```json\n{"entities":[{"name":"Nebula","type":"系统"}],"relations":[]}\n```'
    extraction = parse_extraction(raw, "doc-01#1")
    assert extraction.entities == [{"name": "Nebula", "type": "系统"}]


def test_parse_extraction_handles_surrounding_prose() -> None:
    raw = '好的，结果如下：{"entities":[],"relations":[]} 以上。'
    assert parse_extraction(raw, "doc-01#1").entities == []


def test_parse_extraction_survives_broken_json() -> None:
    extraction = parse_extraction("这不是 JSON", "doc-01#1")
    assert extraction.chunk_id == "doc-01#1"
    assert extraction.entities == [] and extraction.relations == []


def test_parse_extraction_drops_entries_without_names() -> None:
    raw = json.dumps(
        {
            "entities": [{"name": "", "type": "系统"}, {"name": "Nebula", "type": "系统"}],
            "relations": [{"source": "", "target": "Nebula", "predicate": "x"}],
        },
        ensure_ascii=False,
    )
    extraction = parse_extraction(raw, "doc-01#1")
    assert [item["name"] for item in extraction.entities] == ["Nebula"]
    assert extraction.relations == []


def _extraction(chunk_id: str, entities: list[dict], relations: list[dict]) -> Extraction:
    return Extraction(chunk_id=chunk_id, entities=entities, relations=relations)


def test_aliases_merge_into_one_node() -> None:
    graph = KnowledgeGraph.from_extractions(
        [
            _extraction(
                "doc-01#1",
                [
                    {"name": "Nebula", "type": "系统", "aliases": ["Nebula 平台"]},
                    {"name": "Nebula 平台", "type": "系统"},
                ],
                [],
            )
        ]
    )
    assert len(graph.entities) == 1
    entity = next(iter(graph.entities.values()))
    assert entity.name == "Nebula"
    # 名称规范化会去掉空白，别名因此以「Nebula平台」形态挂在规范名上
    assert entity.aliases == ["Nebula平台"]
    assert entity.chunk_ids == ["doc-01#1"]


def test_relations_merge_and_dedupe_chunk_evidence() -> None:
    graph = KnowledgeGraph.from_extractions(
        [
            _extraction(
                "doc-01#1",
                [{"name": "CR-2", "type": "流程"}, {"name": "安全审批人", "type": "角色"}],
                [{"source": "CR-2", "target": "安全审批人", "predicate": "审批"}],
            ),
            _extraction(
                "doc-02#1",
                [{"name": "CR-2", "type": "流程"}, {"name": "安全审批人", "type": "角色"}],
                [{"source": "CR-2", "target": "安全审批人", "predicate": "审批"}],
            ),
        ]
    )
    assert len(graph.relations) == 1
    relation = graph.relations[0]
    assert relation.chunk_ids == ["doc-01#1", "doc-02#1"]
    assert relation.weight == 2


def test_self_loops_and_unknown_endpoints_are_dropped() -> None:
    graph = KnowledgeGraph.from_extractions(
        [
            _extraction(
                "doc-01#1",
                [{"name": "Nebula", "type": "系统"}],
                [
                    {"source": "Nebula", "target": "Nebula", "predicate": "等于"},
                    {"source": "Nebula", "target": "不存在的实体", "predicate": "关联"},
                ],
            )
        ]
    )
    assert graph.relations == []


def test_expand_respects_hop_limit(graph: KnowledgeGraph) -> None:
    one_hop = graph.expand(["CR-2"], hops=1)
    two_hops = graph.expand(["CR-2"], hops=2)
    assert set(one_hop) <= set(two_hops)
    assert graph.expand(["不存在"], hops=2) == {}


def test_chunks_for_entities_prefers_near_entities(graph: KnowledgeGraph) -> None:
    distance = graph.expand(["Nebula"], hops=2)
    scores = graph.chunks_for_entities(distance)
    assert scores, "多跳展开应至少覆盖一个 chunk"
    # 种子自身的出处得满分，图上更远的实体按其距离衰减叠加
    assert scores["doc-01#1"] >= 1.0
    assert all(score > 0 for score in scores.values())


def test_search_names_prefers_longest_match(graph: KnowledgeGraph) -> None:
    names = graph.search_names("Nebula 平台的高风险变更由谁审批")
    assert names[0] == "Nebula"


def test_link_entities_by_lexical_falls_back_to_token_overlap(graph: KnowledgeGraph) -> None:
    linked = link_entities_by_lexical("审批人怎么改", graph)
    assert "安全审批人" in linked


def test_relation_paths_finds_cross_document_chain(graph: KnowledgeGraph) -> None:
    paths = graph.relation_paths("Nebula", "张岚", max_hops=3)
    assert paths, "应能找到 Nebula → CR-2 → 安全审批人 → 张岚"
    assert any("CR-2" in path for path in paths)


def test_graph_roundtrip_through_disk(tmp_path: Path, graph: KnowledgeGraph) -> None:
    path = tmp_path / "graph.json"
    graph.save(path)
    reloaded = KnowledgeGraph.load(path)
    assert set(reloaded.entities) == set(graph.entities)
    assert len(reloaded.relations) == len(graph.relations)
    # 邻接表与 chunk 反向索引都在装载时重建
    assert reloaded.adjacency["Nebula"] == graph.adjacency["Nebula"]
    assert reloaded.chunk_entities["doc-03#1"]


def test_stats_counts_entity_types(graph: KnowledgeGraph) -> None:
    stats = graph.stats()
    assert stats["entities"] >= 4
    assert stats["relations"] >= 3
    assert stats["type_系统"] >= 1


def test_extract_chunks_uses_cache_second_time(tmp_path: Path, corpus_dir: Path) -> None:
    from graphrag_mvp.corpus import load_corpus
    from graphrag_mvp.graph import ExtractionCache, extract_chunks

    chunks = load_corpus(corpus_dir)
    llm = ScriptedLLM(default='{"entities":[],"relations":[]}')
    cache = ExtractionCache(tmp_path / "extractions.jsonl")
    extract_chunks(chunks, llm, cache=cache, workers=2)
    calls_after_first = len(llm.calls)
    extract_chunks(chunks, llm, cache=ExtractionCache(tmp_path / "extractions.jsonl"), workers=2)
    assert len(llm.calls) == calls_after_first, "第二次应从缓存读取，不再调用模型"


def test_extract_chunks_degrades_on_failure(corpus_dir: Path) -> None:
    from graphrag_mvp.corpus import load_corpus
    from graphrag_mvp.graph import extract_chunks

    class Failing:
        def complete(self, *args, **kwargs):
            raise RuntimeError("boom")

    chunks = load_corpus(corpus_dir)
    extractions = extract_chunks(chunks, Failing(), workers=2, max_retries=0)
    assert len(extractions) == len(chunks)
    assert all(not extraction.entities for extraction in extractions.values())


def test_extraction_cache_invalidates_on_text_change(tmp_path: Path, corpus_dir: Path) -> None:
    from graphrag_mvp.corpus import load_corpus
    from graphrag_mvp.graph import ExtractionCache

    chunks = load_corpus(corpus_dir)
    cache = ExtractionCache(tmp_path / "e.jsonl")
    cache.put(chunks[0], Extraction(chunk_id=chunks[0].chunk_id, entities=[{"name": "X"}]))
    assert cache.get(chunks[0]) is not None
    changed = chunks[0].__class__(**{**chunks[0].to_dict(), "text": chunks[0].text + "新增一句话"})
    assert cache.get(changed) is None


def test_json_dumps_of_extraction_is_serializable() -> None:
    extraction = Extraction(chunk_id="doc-01#1", entities=[{"name": "Nebula", "type": "系统"}])
    assert json.loads(json.dumps(extraction.to_dict(), ensure_ascii=False))["chunk_id"] == "doc-01#1"
