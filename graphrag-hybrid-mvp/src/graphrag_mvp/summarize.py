"""社区摘要：用 LLM 把每个社区压成一段「宏观事实」。

摘要不是装饰品 —— 它是混合检索的第四个通道（community），
负责回答「跨系统的整体要求是什么」这类无法靠单个 chunk 命中的问题。
摘要同样落盘缓存：社区成员哈希不变就不重复调用模型。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .llm import LLM
from .types import Chunk, Community

SUMMARY_SYSTEM = """你负责为企业知识图谱的「话题簇」写摘要。你会看到该簇包含的实体清单与若干原文片段。
请输出 JSON：{"summary": "..."}
要求：
1. 摘要不超过 150 个汉字，写清这个簇在讲什么、涉及哪些系统/角色/流程、有哪些硬性约束（编号、阈值、责任角色）。
2. 只依据给定片段，不要补充常识、不要评价。
3. 不要罗列片段编号，用概括性陈述。"""


def _chunk_digest(entities: Sequence[str], chunk_ids: Sequence[str]) -> str:
    payload = json.dumps({"e": list(entities), "c": list(chunk_ids)}, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _build_prompt(community: Community, chunks: dict[str, Chunk], max_chunks: int = 6) -> str:
    lines = [f"实体清单：{'、'.join(community.entities)}", ""]
    for chunk_id in community.chunk_ids[:max_chunks]:
        chunk = chunks.get(chunk_id)
        if chunk is None:
            continue
        lines.append(f"[{chunk_id}]《{chunk.doc_title}》§{chunk.section}")
        lines.append(chunk.text[:400])
        lines.append("")
    return "\n".join(lines)


class SummaryCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, str] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    self.entries[record["key"]] = record["summary"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for key in sorted(self.entries):
                handle.write(
                    json.dumps({"key": key, "summary": self.entries[key]}, ensure_ascii=False) + "\n"
                )
        tmp.replace(self.path)


def summarize_communities(
    communities: Sequence[Community],
    chunks: dict[str, Chunk],
    llm: LLM,
    *,
    cache: SummaryCache | None = None,
    workers: int = 4,
    max_chunks: int = 6,
) -> list[Community]:
    """原地填充 summary 字段并返回；单簇失败降级为「实体清单」文本，不阻断构建。"""

    def work(community: Community) -> Community:
        key = _chunk_digest(community.entities, community.chunk_ids)
        if cache and key in cache.entries:
            community.summary = cache.entries[key]
            return community
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": _build_prompt(community, chunks, max_chunks)},
        ]
        try:
            response = llm.complete(messages, json_mode=True, max_tokens=600)
            payload = json.loads(response.text)
            summary = str(payload.get("summary", "")).strip()
        except Exception:  # noqa: BLE001 - 摘要失败不影响局部检索
            summary = ""
        if not summary:
            summary = f"围绕 {'、'.join(community.entities[:6])} 的约束与流程。"
        community.summary = summary
        if cache:
            cache.entries[key] = summary
        return community

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        communities = list(pool.map(work, communities))
    if cache:
        cache.save()
    return communities
