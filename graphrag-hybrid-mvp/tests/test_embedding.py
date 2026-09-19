"""向量通道：哈希嵌入的确定性、缓存增量与余弦排序。"""

from __future__ import annotations

import math
from pathlib import Path

from graphrag_mvp.embedding import DenseIndex, EmbeddingStore, HashingEmbedder
from graphrag_mvp.types import Chunk


def _chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id, doc_id=chunk_id.split("#")[0], doc_title="t", section="s", text=text, ordinal=1
    )


def test_hashing_embedder_is_deterministic() -> None:
    embedder = HashingEmbedder(dim=64)
    assert embedder.embed_query("Nebula 发布窗口") == embedder.embed_query("Nebula 发布窗口")


def test_hashing_embedder_returns_normalized_vectors_of_fixed_dim() -> None:
    embedder = HashingEmbedder(dim=128)
    vector = embedder.embed_documents(["审批流程"])[0]
    assert len(vector) == 128
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, rel_tol=1e-9)


def test_hashing_embedder_similar_text_scores_higher() -> None:
    embedder = HashingEmbedder(dim=512)
    query = embedder.embed_query("Nebula 平台的高风险变更")
    close = embedder.embed_documents(["Nebula 平台高风险变更需审批"])[0]
    far = embedder.embed_documents(["Atlas 数据仓库的保留周期是 180 天"])[0]

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, close) > cosine(query, far)


def test_embedding_store_roundtrip(tmp_path: Path) -> None:
    store = EmbeddingStore(path=tmp_path / "emb.jsonl")
    chunks = [_chunk("doc-01#1", "甲"), _chunk("doc-01#2", "乙")]
    store.build(chunks, HashingEmbedder(dim=32))
    store.save()

    reloaded = EmbeddingStore.load(tmp_path / "emb.jsonl")
    assert set(reloaded.vectors) == {"doc-01#1", "doc-01#2"}
    assert reloaded.dim == 32
    assert reloaded.vectors["doc-01#1"] == store.vectors["doc-01#1"]


def test_embedding_store_only_computes_missing_chunks(tmp_path: Path) -> None:
    store = EmbeddingStore(path=tmp_path / "emb.jsonl")
    embedder = HashingEmbedder(dim=16)
    first = [_chunk("doc-01#1", "甲")]
    assert store.build(first, embedder) == 1
    # 已有 chunk 不重算，新 chunk 才计算
    second = [*first, _chunk("doc-01#2", "乙")]
    assert store.build(second, embedder) == 1


def test_embedding_store_rebuilds_when_model_changes(tmp_path: Path) -> None:
    store = EmbeddingStore(path=tmp_path / "emb.jsonl")
    chunks = [_chunk("doc-01#1", "甲")]
    store.build(chunks, HashingEmbedder(dim=16))
    # 换了模型/维度 → 旧向量必须整体失效，否则检索结果会静默错乱
    assert store.build(chunks, HashingEmbedder(dim=32)) == 1
    assert store.dim == 32
    assert len(store.vectors["doc-01#1"]) == 32


def test_dense_index_orders_by_cosine_similarity() -> None:
    embedder = HashingEmbedder(dim=256)
    chunks = [
        _chunk("doc-01#1", "Nebula 平台高风险变更必须走 CR-2 审批"),
        _chunk("doc-02#1", "Atlas 数据仓库保留周期 180 天"),
    ]
    store = EmbeddingStore(path=Path("/dev/null"))
    store.build(chunks, embedder)
    index = DenseIndex(chunks, store)
    hits = index.search(embedder.embed_query("高风险变更谁审批"), top_k=2)
    assert hits[0].chunk_id == "doc-01#1"
    assert hits[0].rank == 1
    assert hits[0].channel == "dense"
    assert hits[0].score >= hits[1].score
