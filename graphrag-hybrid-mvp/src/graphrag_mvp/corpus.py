"""语料装载与小节切分。

切分策略（heading-aware，而不是定长滑窗）：
1. 以 Markdown 二级/三级标题为边界切小节 —— 制度类文档的事实天然按条款聚集，
   按标题切能保证「一个 chunk = 一条可引用的事实」。
2. 过长的节在段落边界二次切分，过短的节向后合并，避免出现半句话的 chunk。
3. chunk_id = `<doc_id>#<序号>`，稳定、可读、可直接作为回答引用锚点。
"""

from __future__ import annotations

import re
from pathlib import Path

from .types import Chunk

_MAX_CHARS = 700
_MIN_CHARS = 180
_HEADING_RE = re.compile(r"^(#{2,4})\s+(.*)$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 YAML frontmatter 的扁平键值（MVP 只用扁平标量，不引入 YAML 依赖）。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[match.end() :]


def _split_sections(body: str) -> list[tuple[str, str]]:
    """按标题切分为 (section_title, text) 列表；无标题内容归入「正文」。"""
    sections: list[tuple[str, list[str]]] = []
    current_title = "正文"
    current_lines: list[str] = []
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if any(part.strip() for part in current_lines):
                sections.append((current_title, current_lines))
            current_title = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if any(part.strip() for part in current_lines):
        sections.append((current_title, current_lines))
    return [(title, "\n".join(lines).strip()) for title, lines in sections]


def _split_long(text: str, max_chars: int = _MAX_CHARS) -> list[str]:
    """过长小节在空行（段落）边界二次切分，保持段落完整。"""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buffer: list[str] = []
    size = 0
    for paragraph in text.split("\n\n"):
        if size and size + len(paragraph) > max_chars:
            parts.append("\n\n".join(buffer).strip())
            buffer, size = [], 0
        buffer.append(paragraph)
        size += len(paragraph) + 2
    if buffer:
        parts.append("\n\n".join(buffer).strip())
    return [part for part in parts if part]


def chunk_document(doc_id: str, title: str, body: str) -> list[Chunk]:
    """把一篇文档切成 Chunk 列表；过短的小节向后合并，避免碎片。"""
    raw: list[tuple[str, str]] = []
    for section, text in _split_sections(body):
        for piece in _split_long(text):
            raw.append((section, piece))

    merged: list[tuple[str, str]] = []
    for section, piece in raw:
        if merged and len(piece) < _MIN_CHARS and merged[-1][0] == section:
            prev_section, prev_text = merged[-1]
            merged[-1] = (prev_section, f"{prev_text}\n\n{piece}")
        else:
            merged.append((section, piece))

    chunks: list[Chunk] = []
    for index, (section, text) in enumerate(merged, start=1):
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{index}",
                doc_id=doc_id,
                doc_title=title,
                section=section,
                text=text,
                ordinal=index,
            )
        )
    return chunks


def load_corpus(corpus_dir: Path) -> list[Chunk]:
    """装载目录下所有 *.md，按 doc_id 排序输出 Chunk。"""
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"语料目录不存在: {corpus_dir}")
    chunks: list[Chunk] = []
    for path in sorted(corpus_dir.glob("*.md")):
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        doc_id = meta.get("id", path.stem)
        title = meta.get("title", path.stem)
        chunks.extend(chunk_document(doc_id, title, body))
    if not chunks:
        raise ValueError(f"语料目录里没有可用文档: {corpus_dir}")
    return chunks


def doc_ids(chunks: list[Chunk]) -> list[str]:
    seen: list[str] = []
    for chunk in chunks:
        if chunk.doc_id not in seen:
            seen.append(chunk.doc_id)
    return seen
