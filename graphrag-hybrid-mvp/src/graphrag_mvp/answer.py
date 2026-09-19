"""回答层：把融合后的上下文拼成带编号的证据块，交给 DeepSeek 生成带引用的答案。

反幻觉的三条硬约束（都写在 system prompt 里，且有对应的离线断言）：
1. 只依据给定资料作答；资料不足时明确说「资料不足」，禁止用常识补全。
2. 每个结论句后必须跟引用编号 [n]，n 对应证据块编号；编号之外不许出现其它引用。
3. 不允许把不同文档的条款缝合出原文没有的因果（跨文档合并必须显式写清来源）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .llm import LLM
from .types import Answer, Community, RetrievalResult

ANSWER_SYSTEM = """你是企业内部知识库问答助手，服务对象是需要精确条款的工程与合规同事。

规则：
1. 只使用「资料」中的内容回答，资料没写的一律回答「资料不足」。禁止使用常识或推测补全。
2. 每个结论后必须标注来源编号，格式为 [1] [2]；一条结论可以标多个编号。
3. 编号只能取自资料里给出的编号，不得虚构编号，也不得引用没有给出的文档。
4. 如果是多跳问题（需要把几篇资料的条款串起来才能得到结论），请先用一句话说明推理链：
   「A 规定… → B 规定… → 因此…」，再给结论。
5. 直接给答案，不要复述问题、不要写总结陈词。中文回答，控制在 200 字以内。"""


def build_answer_messages(question: str, retrieval: RetrievalResult) -> list[dict[str, str]]:
    lines: list[str] = []
    for position, chunk in enumerate(retrieval.chunks, start=1):
        lines.append(f"[{position}] 来源 {chunk.chunk_id} 《{chunk.doc_title}》§{chunk.section}")
        lines.append(chunk.text.strip())
        lines.append("")
    if retrieval.communities:
        lines.append("（以下为跨文档话题簇摘要，可作为背景，引用时仍请引用上面的编号）")
        for community in retrieval.communities:
            if community.summary:
                lines.append(f"- [{community.community_id}] {community.summary}")
        lines.append("")
    user = f"问题：{question}\n\n资料：\n" + "\n".join(lines).strip()
    return [{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": user}]


_CITATION_RE = re.compile(r"\[(\d+)\]")
_DOC_ID_RE = re.compile(r"\b(doc-\d{2,})\b")


def extract_citations(answer_text: str, retrieval: RetrievalResult) -> list[str]:
    """把回答里的 [n] 映射回文档 id；顺带捞正文里直接写出的 doc-xx 编号。"""
    cited_docs: list[str] = []
    chunks = retrieval.chunks
    for number in _CITATION_RE.findall(answer_text):
        index = int(number) - 1
        if 0 <= index < len(chunks) and chunks[index].doc_id not in cited_docs:
            cited_docs.append(chunks[index].doc_id)
    for doc_id in _DOC_ID_RE.findall(answer_text):
        if doc_id not in cited_docs and any(chunk.doc_id == doc_id for chunk in chunks):
            cited_docs.append(doc_id)
    return cited_docs


def answer_question(
    question: str,
    retrieval: RetrievalResult,
    llm: LLM,
    *,
    max_tokens: int = 800,
) -> Answer:
    response = llm.complete(
        build_answer_messages(question, retrieval), json_mode=False, max_tokens=max_tokens
    )
    usage = {
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
    }
    return Answer(
        question=question,
        text=response.text.strip(),
        citations=extract_citations(response.text, retrieval),
        retrieval=retrieval,
        usage=usage,
    )


def format_evidence(retrieval: RetrievalResult, limit: int = 5) -> str:
    """终端展示用的证据清单：命中来源 + 通道 + 排名。"""
    rows: list[str] = []
    for hit in retrieval.hits[:limit]:
        chunk = next((c for c in retrieval.chunks if c.chunk_id == hit.chunk_id), None)
        title = chunk.doc_title if chunk else "?"
        channels = ",".join(f"{name}#{rank}" for name, rank in sorted(hit.channels.items()))
        rows.append(f"  {hit.rank}. {hit.chunk_id} 《{title}》 score={hit.score:.5f} 通道={channels}")
    if retrieval.linked_entities:
        rows.append(f"  链接实体：{'、'.join(retrieval.linked_entities[:8])}")
    return "\n".join(rows)


def summarize_communities_for_display(communities: Sequence[Community]) -> str:
    return "\n".join(f"  {c.community_id} ({len(c.entities)} 实体): {c.summary}" for c in communities)
