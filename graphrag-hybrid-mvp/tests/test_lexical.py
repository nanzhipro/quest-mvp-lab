"""词法通道：分词与 BM25 的行为契约。"""

from __future__ import annotations

from graphrag_mvp.lexical import BM25Index, tokenize
from graphrag_mvp.types import Chunk


def _chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id, doc_id=chunk_id.split("#")[0], doc_title="t", section="s", text=text, ordinal=1
    )


def test_tokenize_default_mode_is_bigram() -> None:
    tokens = tokenize("审批流程")
    assert tokens == ["审批", "批流", "流程"]


def test_tokenize_unigram_bigram_mode_adds_single_chars() -> None:
    tokens = tokenize("审批流程", mode="unigram_bigram")
    assert "审批" in tokens and "流程" in tokens
    assert "审" in tokens and "批" in tokens


def test_tokenize_rejects_unknown_mode() -> None:
    import pytest

    with pytest.raises(ValueError, match="未知分词模式"):
        tokenize("审批", mode="char-word")


def test_tokenize_keeps_latin_words_and_numbers_intact() -> None:
    tokens = tokenize("CR-2 变更在 21:00 生效，Nebula 平台")
    assert "cr-2" in tokens
    assert "21" in tokens and "00" in tokens
    assert "nebula" in tokens


def test_tokenize_drops_stopwords() -> None:
    assert "的" not in tokenize("审批的流程")


def test_bm25_index_records_lexical_mode(corpus_dir) -> None:
    from graphrag_mvp.corpus import load_corpus

    index = BM25Index(load_corpus(corpus_dir), mode="unigram_bigram")
    assert index.mode == "unigram_bigram"
    assert BM25Index(load_corpus(corpus_dir)).mode == "bigram"


def test_bm25_ranks_term_match_first(corpus_dir) -> None:
    from graphrag_mvp.corpus import load_corpus

    index = BM25Index(load_corpus(corpus_dir))
    hits = index.search("发布窗口", top_k=3)
    assert hits, "应至少命中一条"
    assert hits[0].channel == "lexical"
    assert hits[0].rank == 1
    assert "doc-01" in hits[0].chunk_id


def test_bm25_drops_zero_score_chunks() -> None:
    index = BM25Index([_chunk("doc-01#1", "Nebula 平台发布窗口")])
    assert index.search("完全无关的词汇xyz") == []


def test_bm25_idf_penalizes_common_terms() -> None:
    chunks = [
        _chunk("doc-01#1", "审批 审批"),
        _chunk("doc-02#1", "审批 备货"),
        _chunk("doc-03#1", "审批 归档"),
    ]
    index = BM25Index(chunks)
    # 「审批」出现在全部 3 个 chunk，「备货」只出现 1 次 → 稀有词 IDF 更高
    assert index.idf("备货") > index.idf("审批")


def test_bm25_ranks_are_contiguous_and_descending() -> None:
    chunks = [_chunk(f"doc-0{i}#1", f"发布窗口 第{i}条") for i in range(1, 4)]
    index = BM25Index(chunks)
    hits = index.search("发布窗口", top_k=3)
    assert [hit.rank for hit in hits] == [1, 2, 3]
    assert hits[0].score >= hits[-1].score


def test_bm25_respects_top_k() -> None:
    chunks = [_chunk(f"doc-0{i}#1", "审批流程") for i in range(1, 5)]
    index = BM25Index(chunks)
    assert len(index.search("审批", top_k=2)) == 2
