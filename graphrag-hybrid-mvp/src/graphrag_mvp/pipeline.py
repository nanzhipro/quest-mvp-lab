"""索引构建与装载：把语料变成可检索的四通道产物。

产物目录（默认为 `artifacts/`，整体可删除重建）：

    artifacts/
    ├── chunks.jsonl          切分后的 chunk（检索与引用的最小单元）
    ├── embeddings.jsonl      模型名 + 维度 + chunk 向量（按模型名失效重算）
    ├── graph.json            归并后的实体与关系
    ├── communities.json      社区结构与它们的 LLM 摘要
    ├── extractions.jsonl     逐 chunk 的原始抽取结果（正文哈希变化才重抽）
    └── summaries.jsonl        社区摘要缓存（社区成员不变则不重复调用模型）

每一步都幂等：重复构建只补算缺失部分，因此「改一篇文档 → 增量重建」成本很低。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .communities import build_communities, load_communities, save_communities
from .corpus import doc_ids, load_corpus
from .embedding import DenseIndex, Embedder, EmbeddingStore
from .graph import ExtractionCache, KnowledgeGraph, extract_chunks
from .lexical import BM25Index
from .llm import LLM
from .retriever import RetrieverIndex
from .summarize import SummaryCache, summarize_communities
from .types import Chunk

CHUNKS_FILE = "chunks.jsonl"
EMBEDDINGS_FILE = "embeddings.jsonl"
GRAPH_FILE = "graph.json"
COMMUNITIES_FILE = "communities.json"
EXTRACTIONS_FILE = "extractions.jsonl"
SUMMARIES_FILE = "summaries.jsonl"


@dataclass
class BuildReport:
    docs: int = 0
    chunks: int = 0
    entities: int = 0
    relations: int = 0
    communities: int = 0
    summaries: int = 0
    embedded_chunks: int = 0
    extracted_chunks: int = 0
    duration_s: float = 0.0
    artifacts_dir: str = ""
    embedder: str = ""
    llm_stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "docs": self.docs,
            "chunks": self.chunks,
            "entities": self.entities,
            "relations": self.relations,
            "communities": self.communities,
            "summaries": self.summaries,
            "embedded_chunks": self.embedded_chunks,
            "extracted_chunks": self.extracted_chunks,
            "duration_s": round(self.duration_s, 2),
            "embedder": self.embedder,
            "llm_stats": self.llm_stats,
        }


def save_chunks(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_chunks(path: Path) -> list[Chunk]:
    if not path.is_file():
        raise FileNotFoundError(f"未找到索引产物 {path}，请先运行 `graphrag-mvp build`")
    return [
        Chunk.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_index(
    corpus_dir: Path,
    artifacts_dir: Path,
    *,
    llm: LLM,
    embedder: Embedder,
    workers: int = 6,
    min_community_size: int = 2,
    progress: Callable[[str], None] = lambda message: None,
) -> BuildReport:
    """四通道索引的完整构建流程（幂等、可增量）。"""
    started = time.time()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    chunks = load_corpus(corpus_dir)
    save_chunks(chunks, artifacts_dir / CHUNKS_FILE)
    progress(f"语料：{len(doc_ids(chunks))} 篇文档 → {len(chunks)} 个 chunk")

    # 1) 稠密通道
    store = EmbeddingStore.load(artifacts_dir / EMBEDDINGS_FILE)
    computed = store.build(chunks, embedder)
    store.save()
    progress(f"向量：{len(store.vectors)} 条（本次新算 {computed} 条，模型 {embedder.name}）")

    # 2) 图谱通道（LLM 抽取 + 归并）
    extraction_cache = ExtractionCache(artifacts_dir / EXTRACTIONS_FILE)
    extractions = extract_chunks(chunks, llm, cache=extraction_cache, workers=workers)
    graph = KnowledgeGraph.from_extractions(extractions.values())
    graph.save(artifacts_dir / GRAPH_FILE)
    stats = graph.stats()
    progress(
        f"图谱：{stats['entities']} 个实体 / {stats['relations']} 条关系"
        f"（孤立节点 {stats['isolated_entities']}）"
    )

    # 3) 社区 + 摘要
    communities = build_communities(graph, min_size=min_community_size)
    summary_cache = SummaryCache(artifacts_dir / SUMMARIES_FILE)
    communities = summarize_communities(
        communities, {chunk.chunk_id: chunk for chunk in chunks}, llm, cache=summary_cache, workers=workers
    )
    save_communities(communities, artifacts_dir / COMMUNITIES_FILE)
    progress(f"社区：{len(communities)} 个（已生成摘要 {sum(1 for c in communities if c.summary)} 条）")

    report = BuildReport(
        docs=len(doc_ids(chunks)),
        chunks=len(chunks),
        entities=stats["entities"],
        relations=stats["relations"],
        communities=len(communities),
        summaries=sum(1 for community in communities if community.summary),
        embedded_chunks=computed,
        extracted_chunks=len(chunks),
        duration_s=time.time() - started,
        artifacts_dir=str(artifacts_dir),
        embedder=embedder.name,
    )
    stats_attr = getattr(llm, "stats", None)
    if isinstance(stats_attr, dict):
        report.llm_stats = dict(stats_attr)
    return report


def load_retriever_index(
    artifacts_dir: Path,
    *,
    embedder: Embedder | None = None,
    chunks_path: Path | None = None,
    lexical_mode: str = "bigram",
) -> RetrieverIndex:
    """装载索引用于检索；未提供 embedder 时自动关闭稠密通道。"""
    chunks = load_chunks(chunks_path or artifacts_dir / CHUNKS_FILE)
    bm25 = BM25Index(chunks, mode=lexical_mode)
    dense: DenseIndex | None = None
    if embedder is not None:
        store = EmbeddingStore.load(artifacts_dir / EMBEDDINGS_FILE)
        if store.vectors:
            dense = DenseIndex(chunks, store)
    graph = (
        KnowledgeGraph.load(artifacts_dir / GRAPH_FILE) if (artifacts_dir / GRAPH_FILE).is_file() else None
    )
    communities = load_communities(artifacts_dir / COMMUNITIES_FILE)
    return RetrieverIndex(
        chunks=chunks,
        bm25=bm25,
        dense=dense,
        graph=graph,
        communities=communities,
        embedder=embedder if dense is not None else None,
    )
