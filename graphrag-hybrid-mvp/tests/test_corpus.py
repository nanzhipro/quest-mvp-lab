"""语料装载与切分的行为契约。"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag_mvp.corpus import chunk_document, doc_ids, load_corpus, parse_frontmatter
from graphrag_mvp.types import Chunk


def test_parse_frontmatter_reads_flat_keys() -> None:
    meta, body = parse_frontmatter("---\nid: doc-09\ntitle: 测试文档\nowner: 基础架构部\n---\n\n正文\n")
    assert meta == {"id": "doc-09", "title": "测试文档", "owner": "基础架构部"}
    assert body.strip() == "正文"


def test_parse_frontmatter_without_block_returns_whole_text() -> None:
    meta, body = parse_frontmatter("# 无 frontmatter\n\n内容")
    assert meta == {}
    assert body.startswith("# 无 frontmatter")


def test_chunk_ids_are_stable_and_readable() -> None:
    chunks = chunk_document("doc-07", "标题", "## 甲\n\n内容甲\n\n## 乙\n\n内容乙")
    assert [chunk.chunk_id for chunk in chunks] == ["doc-07#1", "doc-07#2"]
    assert [chunk.section for chunk in chunks] == ["甲", "乙"]
    assert all(chunk.doc_id == "doc-07" and chunk.doc_title == "标题" for chunk in chunks)


def test_chunk_long_section_splits_on_paragraph_boundary() -> None:
    paragraph = "这是一段用于测试切分的长文本，包含足够多的字符以触发二次切分。" * 12
    body = "## 长节\n\n" + "\n\n".join([paragraph] * 3)
    chunks = chunk_document("doc-07", "标题", body)
    assert len(chunks) >= 2
    assert all(len(chunk.text) <= 900 for chunk in chunks)
    assert all(chunk.section == "长节" for chunk in chunks)


def test_short_sections_merge_forward() -> None:
    body = "## 甲\n\n短。\n\n## 甲\n\n又一段很短的补充。"
    chunks = chunk_document("doc-07", "标题", body)
    assert len(chunks) == 1
    assert "又一段很短的补充" in chunks[0].text


def test_body_without_headings_becomes_single_section() -> None:
    chunks = chunk_document("doc-07", "标题", "没有标题的一段话。")
    assert len(chunks) == 1
    assert chunks[0].section == "正文"


def test_load_corpus_sorts_by_file_name_and_assigns_ids(corpus_dir: Path) -> None:
    chunks = load_corpus(corpus_dir)
    assert doc_ids(chunks) == ["doc-01", "doc-02", "doc-03"]
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert chunks[0].chunk_id.startswith("doc-01#")


def test_load_corpus_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_corpus(tmp_path / "nope")


def test_load_corpus_rejects_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        load_corpus(tmp_path / "empty")


def test_chunk_label_is_citable() -> None:
    chunk = Chunk(
        chunk_id="doc-01#2", doc_id="doc-01", doc_title="标题", section="小节", text="内容", ordinal=2
    )
    assert chunk.label == "[doc-01#2]《标题》§小节"
