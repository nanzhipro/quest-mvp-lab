"""跨模块共享的数据契约。

这些 dataclass 是各层的唯一接口语言：语料层产出 Chunk，抽取层产出 Entity/Relation，
融合层消费各通道的 RankedHit，回答层消费 RetrievalResult。
所有持久化产物都是这些结构的 JSON 序列化，字段名即契约，不随实现变动。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Chunk:
    """最小检索单元：一篇文档的一个语义小节。"""

    chunk_id: str  # 形如 doc-05#2，稳定且可读，作为引用锚点
    doc_id: str
    doc_title: str
    section: str
    text: str
    ordinal: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        return cls(**data)

    @property
    def label(self) -> str:
        return f"[{self.chunk_id}]《{self.doc_title}》§{self.section}"


@dataclass
class Entity:
    """图谱节点：规范名 + 别名集合 + 出处 chunk。"""

    name: str
    type: str
    aliases: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Relation:
    """图谱边：source --predicate--> target，带出处与支持度。"""

    source: str
    target: str
    predicate: str
    chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def weight(self) -> int:
        return len(self.chunk_ids)


@dataclass
class Community:
    """社区：图上的稠密子图，代表一个跨文档的话题簇。"""

    community_id: str
    entities: list[str]
    chunk_ids: list[str]
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Community:
        return cls(**data)


@dataclass(frozen=True)
class RankedHit:
    """单个检索通道的一次命中。"""

    chunk_id: str
    score: float
    rank: int
    channel: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FusedHit:
    """融合后的命中：score 为 RRF 加权分，channels 记录贡献来源（可审计）。"""

    chunk_id: str
    score: float
    rank: int
    channels: dict[str, int] = field(default_factory=dict)  # channel -> 该通道内排名

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    question: str
    hits: list[FusedHit] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    communities: list[Community] = field(default_factory=list)
    linked_entities: list[str] = field(default_factory=list)
    per_channel: dict[str, list[RankedHit]] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    channels: list[str] = field(default_factory=list)

    @property
    def context_chunk_ids(self) -> list[str]:
        return [chunk.chunk_id for chunk in self.chunks]

    @property
    def context_doc_ids(self) -> list[str]:
        seen: list[str] = []
        for chunk in self.chunks:
            if chunk.doc_id not in seen:
                seen.append(chunk.doc_id)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "channels": self.channels,
            "weights": self.weights,
            "linked_entities": self.linked_entities,
            "hits": [hit.to_dict() for hit in self.hits],
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "communities": [c.to_dict() for c in self.communities],
            "per_channel": {name: [hit.to_dict() for hit in hits] for name, hits in self.per_channel.items()},
        }


@dataclass
class Answer:
    question: str
    text: str
    citations: list[str] = field(default_factory=list)
    retrieval: RetrievalResult | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.text,
            "citations": self.citations,
            "usage": self.usage,
            "retrieval": self.retrieval.to_dict() if self.retrieval else None,
        }
