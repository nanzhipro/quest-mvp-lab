"""索引构建的幂等性与增量行为。"""

from __future__ import annotations

from pathlib import Path

from conftest import DOCS, extraction_for

from graphrag_mvp.embedding import HashingEmbedder
from graphrag_mvp.llm import LLMResponse
from graphrag_mvp.pipeline import (
    CHUNKS_FILE,
    COMMUNITIES_FILE,
    EMBEDDINGS_FILE,
    GRAPH_FILE,
    BuildReport,
    build_index,
    load_chunks,
    load_retriever_index,
)


class CountingLLM:
    """可数调用次数的抽取桩。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, messages, **kwargs) -> LLMResponse:
        system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
        user = "\n".join(m.get("content", "") for m in messages if m.get("role") != "system")
        self.calls.append(user)
        if "话题簇" in system:
            return LLMResponse(text='{"summary": "涉及 Nebula 的改造链。"}')
        return LLMResponse(text=extraction_for(user))


def _build(corpus: Path, artifacts: Path, llm) -> BuildReport:
    return build_index(
        corpus, artifacts, llm=llm, embedder=HashingEmbedder(dim=32), workers=2, progress=lambda _: None
    )


def test_build_writes_all_artifacts(corpus_dir: Path, tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    report = _build(corpus_dir, artifacts, CountingLLM())
    for name in (CHUNKS_FILE, EMBEDDINGS_FILE, GRAPH_FILE, COMMUNITIES_FILE):
        assert (artifacts / name).is_file(), f"缺少产物 {name}"
    assert report.docs == 3
    assert report.chunks >= 3
    assert report.entities >= 4
    assert report.relations >= 3
    assert report.communities >= 1


def test_rebuild_is_incremental_and_calls_no_model(corpus_dir: Path, tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _build(corpus_dir, artifacts, CountingLLM())
    second_llm = CountingLLM()
    report = _build(corpus_dir, artifacts, second_llm)
    assert report.embedded_chunks == 0, "向量应全部命中缓存"
    assert second_llm.calls == [], "抽取与摘要应全部命中缓存，不再调用模型"


def test_adding_a_document_only_embeds_the_new_chunks(corpus_dir: Path, tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    _build(corpus_dir, artifacts, CountingLLM())
    (corpus_dir / "doc-04-new.md").write_text(
        "---\nid: doc-04\ntitle: 新增制度\nowner: 数据平台部\n---\n\n## 要求\n\n"
        "Atlas 数据仓库保留 180 天。\n",
        encoding="utf-8",
    )
    report = _build(corpus_dir, artifacts, CountingLLM())
    assert report.docs == 4
    assert 0 < report.embedded_chunks < report.chunks


def test_chunks_file_matches_corpus(corpus_dir: Path, built_artifacts: Path) -> None:
    chunks = load_chunks(built_artifacts / CHUNKS_FILE)
    assert {chunk.doc_id for chunk in chunks} == {"doc-01", "doc-02", "doc-03"}
    assert len(chunks) >= len(DOCS)
    assert all(chunk.chunk_id.startswith(chunk.doc_id + "#") for chunk in chunks)


def test_load_index_without_embedder_disables_dense(built_artifacts: Path) -> None:
    index = load_retriever_index(built_artifacts, embedder=None)
    assert index.dense is None
    assert index.embedder is None
    assert index.graph is not None and index.communities


def test_load_index_error_message_is_actionable(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError, match="graphrag-mvp build"):
        load_retriever_index(tmp_path / "empty")
