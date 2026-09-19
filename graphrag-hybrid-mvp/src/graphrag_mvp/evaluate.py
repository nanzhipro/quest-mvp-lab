"""检索评测：用带金标准的问题集做通道消融，回答「混合检索到底比单通道强在哪」。

指标定义（文档级，避免 chunk 粒度噪声）：
- recall@k       = |gold_docs ∩ top_k 文档| / |gold_docs|
- all_gold@k     = top_k 是否覆盖全部 gold_docs（多跳问题只有这一项为真才算真答得出）
- MRR            = 1 / 首个 gold 文档的排名

评测只用确定性检索（不调用 LLM），因此可离线复现、可进 CI。
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .retriever import ALL_CHANNELS, RetrieverIndex, retrieve


@dataclass
class QuestionRecord:
    id: str
    question: str
    kind: str
    gold_docs: list[str]
    answer_keywords: list[str] = field(default_factory=list)
    hops: int = 1

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> QuestionRecord:
        return cls(
            id=str(raw["id"]),
            question=str(raw["question"]),
            kind=str(raw.get("kind", "single_hop")),
            gold_docs=[str(doc) for doc in raw.get("gold_docs", [])],  # type: ignore[union-attr]
            answer_keywords=[str(word) for word in raw.get("answer_keywords", [])],  # type: ignore[union-attr]
            hops=int(raw.get("hops", 1)),  # type: ignore[arg-type]
        )


def load_questions(path: Path) -> list[QuestionRecord]:
    return [
        QuestionRecord.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@dataclass
class ConfigResult:
    name: str
    channels: list[str]
    weights: dict[str, float]
    recall_at_k: float
    all_gold_at_k: float
    mrr: float
    recall_single: float
    recall_multi: float
    all_gold_multi: float
    keyword_recall: float = 0.0
    keyword_recall_multi: float = 0.0
    per_question: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "channels": self.channels,
            "weights": self.weights,
            "recall_at_k": round(self.recall_at_k, 4),
            "all_gold_at_k": round(self.all_gold_at_k, 4),
            "mrr": round(self.mrr, 4),
            "recall_single_hop": round(self.recall_single, 4),
            "recall_multi_hop": round(self.recall_multi, 4),
            "all_gold_multi_hop": round(self.all_gold_multi, 4),
            "keyword_recall": round(self.keyword_recall, 4),
            "keyword_recall_multi_hop": round(self.keyword_recall_multi, 4),
            "per_question": self.per_question,
        }


def _score_question(
    index: RetrieverIndex,
    record: QuestionRecord,
    *,
    channels: Sequence[str],
    weights: dict[str, float],
    top_k: int,
    per_channel_k: int,
    graph_hops: int,
    channel_floor: int = 0,
) -> dict[str, Any]:
    result = retrieve(
        index,
        record.question,
        top_k=top_k,
        channels=channels,
        weights=weights,
        per_channel_k=per_channel_k,
        graph_hops=graph_hops,
        channel_floor=channel_floor,
    )
    retrieved_docs = result.context_doc_ids
    gold = set(record.gold_docs)
    matched = [doc for doc in retrieved_docs if doc in gold]
    missing = sorted(gold - set(retrieved_docs))
    first_rank = next((position for position, doc in enumerate(retrieved_docs, start=1) if doc in gold), None)
    context_text = "\n".join(chunk.text for chunk in result.chunks)
    keyword_hits = [word for word in record.answer_keywords if word and word in context_text]
    return {
        "id": record.id,
        "kind": record.kind,
        "hops": record.hops,
        "question": record.question,
        "gold_docs": record.gold_docs,
        "retrieved_docs": retrieved_docs,
        "recall": round(len(matched) / len(gold), 4) if gold else 0.0,
        "all_gold": bool(gold) and not missing,
        "missing_docs": missing,
        "reciprocal_rank": round(1 / first_rank, 4) if first_rank else 0.0,
        # 关键词召回：答案所需的关键事实是否**真的出现在上下文里**（比文档命中更严格）
        "keyword_recall": round(len(keyword_hits) / len(record.answer_keywords), 4)
        if record.answer_keywords
        else 0.0,
        "keywords_present": keyword_hits,
        "linked_entities": result.linked_entities,
    }


@dataclass
class EvalReport:
    top_k: int
    questions: int
    configs: list[ConfigResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "top_k": self.top_k,
            "questions": self.questions,
            "configs": [config.to_dict() for config in self.configs],
        }

    def to_markdown(self) -> str:
        header = (
            "| 通道组合 | recall@K | all-gold@K | 关键词召回 | MRR "
            "| 单跳 recall | 多跳 recall | 多跳 all-gold | 多跳关键词 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
        )
        rows = [
            f"| {config.name} | {config.recall_at_k:.3f} | {config.all_gold_at_k:.3f} | "
            f"{config.keyword_recall:.3f} | {config.mrr:.3f} | {config.recall_single:.3f} | "
            f"{config.recall_multi:.3f} | {config.all_gold_multi:.3f} | {config.keyword_recall_multi:.3f} |"
            for config in self.configs
        ]
        return "\n".join([header, *rows])


DEFAULT_CONFIGS: tuple[tuple[str, tuple[str, ...], dict[str, float]], ...] = (
    ("lexical(BM25)", ("lexical",), {}),
    ("dense(向量)", ("dense",), {}),
    ("graph(图谱)", ("graph",), {}),
    ("lexical+dense", ("lexical", "dense"), {}),
    ("hybrid(四通道)", ALL_CHANNELS, {}),
)


def evaluate(
    index: RetrieverIndex,
    questions: Sequence[QuestionRecord],
    *,
    top_k: int = 8,
    per_channel_k: int = 20,
    graph_hops: int = 2,
    channel_floor: int = 0,
    weights: dict[str, float] | None = None,
    configs: Sequence[tuple[str, Sequence[str], dict[str, float]]] = DEFAULT_CONFIGS,
) -> EvalReport:
    report = EvalReport(top_k=top_k, questions=len(questions))
    default_weights = weights or {}
    for name, channels, overrides in configs:
        merged_weights = {**default_weights, **overrides}
        per_question = [
            _score_question(
                index,
                record,
                channels=channels,
                weights=merged_weights,
                top_k=top_k,
                per_channel_k=per_channel_k,
                graph_hops=graph_hops,
                channel_floor=channel_floor,
            )
            for record in questions
        ]
        single = [item for item in per_question if item["kind"] == "single_hop"]
        multi = [item for item in per_question if item["kind"] != "single_hop"]

        def mean(items: Sequence[dict[str, Any]], key: str) -> float:
            values = [float(item[key]) for item in items]
            return statistics.fmean(values) if values else 0.0

        report.configs.append(
            ConfigResult(
                name=name,
                channels=list(channels),
                weights={name_: float(merged_weights.get(name_, 1.0)) for name_ in channels},
                recall_at_k=mean(per_question, "recall"),
                all_gold_at_k=mean(per_question, "all_gold"),
                mrr=mean(per_question, "reciprocal_rank"),
                recall_single=mean(single, "recall"),
                recall_multi=mean(multi, "recall"),
                all_gold_multi=mean(multi, "all_gold"),
                keyword_recall=mean(per_question, "keyword_recall"),
                keyword_recall_multi=mean(multi, "keyword_recall"),
                per_question=per_question,
            )
        )
    return report


@dataclass
class AnswerScore:
    question_id: str
    kind: str
    keyword_coverage: float
    citation_recall: float
    answer: str
    citations: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.question_id,
            "kind": self.kind,
            "keyword_coverage": round(self.keyword_coverage, 4),
            "citation_recall": round(self.citation_recall, 4),
            "citations": self.citations,
            "answer": self.answer,
        }


def score_answer(record: QuestionRecord, answer_text: str, citations: Sequence[str]) -> AnswerScore:
    """答案级评分：关键词覆盖（答对内容）+ 引用召回（引对了依据）。"""
    covered = [word for word in record.answer_keywords if word and word in answer_text]
    keyword_coverage = len(covered) / len(record.answer_keywords) if record.answer_keywords else 0.0
    gold = set(record.gold_docs)
    citation_hits = [doc for doc in citations if doc in gold]
    citation_recall = len(set(citation_hits)) / len(gold) if gold else 0.0
    return AnswerScore(
        question_id=record.id,
        kind=record.kind,
        keyword_coverage=keyword_coverage,
        citation_recall=citation_recall,
        answer=answer_text,
        citations=list(citations),
    )
