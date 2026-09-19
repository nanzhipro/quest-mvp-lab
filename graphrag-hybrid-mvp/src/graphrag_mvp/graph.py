"""图谱通道的第一半：LLM 抽取实体/关系，并归并成知识图谱。

- 抽取以 chunk 为单位（粒度小 → 提示词短、结果稳、可并行、可增量缓存）。
- 归并阶段做实体规范化与别名合并，否则「Nebula」「Nebula 平台」「Nebula云平台」会变成三个孤立节点，
  多跳检索直接断链 —— 这是 GraphRAG 落地最常见的失败点。
- 图谱与 chunk 双向可追溯：每条边都记着它在哪些 chunk 里被说过，
  因此「图上找到的东西」永远能退回原文引用，不会出现无出处的幻觉节点。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .lexical import tokenize
from .llm import LLM, Message
from .types import Chunk, Entity, Relation

EXTRACTION_SYSTEM = """你是企业知识库的图谱抽取器。输入是一段制度/流程文档片段，
请抽取出其中出现的实体与它们之间的关系。

实体类型只能是以下六种之一：
- 系统：平台、服务、仓库等软件系统（如 Nebula、Kestrel）
- 部门：组织单元（如 基础架构部）
- 角色：岗位或职责身份（如 安全审批人、值班经理）
- 人物：具体的人（如 张岚）
- 流程：带编号或名称的流程（如 CR-2、权限申请流程）
- 制度：规范、策略、要求本身（如 数据保留要求）

关系要求：
1. 只抽取片段中**明确写出**的关系，不要推理、不要脑补常识。
2. source/target 必须是本片段或同一知识库中出现的实体名；predicate 用动词短语，如
   "审批"、"负责"、"依赖"、"触发"、"禁止"、"适用于"。
3. 若片段把某角色/编号与某实体绑定（如"CR-2 由安全审批人审批"），必须产出这条边。

严格输出 JSON，不要输出任何解释文字：
{"entities":[{"name":"...","type":"系统","aliases":["..."]}],
 "relations":[{"source":"...","target":"...","predicate":"..."}]}
若片段中没有可抽取的实体，返回 {"entities":[],"relations":[]}。"""

_TYPE_ALIASES = {
    "系统": "系统",
    "平台": "系统",
    "服务": "系统",
    "system": "系统",
    "部门": "部门",
    "团队": "部门",
    "组织": "部门",
    "角色": "角色",
    "岗位": "角色",
    "人物": "人物",
    "人": "人物",
    "person": "人物",
    "流程": "流程",
    "制度": "制度",
    "规范": "制度",
    "策略": "制度",
}
_VALID_TYPES = {"系统", "部门", "角色", "人物", "流程", "制度"}


def normalize_name(name: str) -> str:
    """实体名规范化：NFKC 折叠全角、去空白、统一括号、去掉尾部「的」。"""
    text = unicodedata.normalize("NFKC", str(name)).strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace("（", "(").replace("）", ")").replace("《", "").replace("》", "")
    if text.endswith("的") and len(text) > 1:
        text = text[:-1]
    return text


def _normalize_type(raw: str) -> str:
    key = normalize_name(raw).lower()
    return _TYPE_ALIASES.get(key, _TYPE_ALIASES.get(normalize_name(raw), "制度"))


@dataclass
class Extraction:
    """单个 chunk 的抽取结果（未归并）。"""

    chunk_id: str
    entities: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"chunk_id": self.chunk_id, "entities": self.entities, "relations": self.relations}


def parse_extraction(text: str, chunk_id: str) -> Extraction:
    """容错解析：模型偶尔会包 markdown 代码块或加前后缀说明。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1:
            return Extraction(chunk_id=chunk_id)
        cleaned = cleaned[start : end + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return Extraction(chunk_id=chunk_id)
    entities = [
        item
        for item in payload.get("entities", [])
        if isinstance(item, dict) and normalize_name(item.get("name", ""))
    ]
    relations = [
        item
        for item in payload.get("relations", [])
        if isinstance(item, dict)
        and normalize_name(item.get("source", ""))
        and normalize_name(item.get("target", ""))
    ]
    return Extraction(chunk_id=chunk_id, entities=entities, relations=relations)


def build_extraction_messages(chunk: Chunk) -> list[Message]:
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {
            "role": "user",
            "content": f"文档《{chunk.doc_title}》 {chunk.chunk_id} §{chunk.section}\n\n{chunk.text}",
        },
    ]


class ExtractionCache:
    """chunk -> 抽取结果 的落盘缓存；正文哈希变化才重抽，语料小改不触发全量重算。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    self._entries[record["chunk_id"]] = record

    @staticmethod
    def _hash(text: str) -> str:
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def get(self, chunk: Chunk) -> Extraction | None:
        record = self._entries.get(chunk.chunk_id)
        if not record or record.get("text_hash") != self._hash(chunk.text):
            return None
        return Extraction(
            chunk_id=chunk.chunk_id,
            entities=record.get("entities", []),
            relations=record.get("relations", []),
        )

    def put(self, chunk: Chunk, extraction: Extraction) -> None:
        self._entries[chunk.chunk_id] = {
            "chunk_id": chunk.chunk_id,
            "text_hash": self._hash(chunk.text),
            "entities": extraction.entities,
            "relations": extraction.relations,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for chunk_id in sorted(self._entries):
                handle.write(json.dumps(self._entries[chunk_id], ensure_ascii=False) + "\n")
        tmp.replace(self.path)


def extract_chunks(
    chunks: Sequence[Chunk],
    llm: LLM,
    *,
    cache: ExtractionCache | None = None,
    workers: int = 6,
    max_retries: int = 2,
    progress: callable | None = None,  # type: ignore[valid-type]
) -> dict[str, Extraction]:
    """并发抽取；单个 chunk 失败降级为空抽取（不因一片失败丢掉整个索引）。"""
    results: dict[str, Extraction] = {}
    pending: list[Chunk] = []
    for chunk in chunks:
        cached = cache.get(chunk) if cache else None
        if cached is not None:
            results[chunk.chunk_id] = cached
        else:
            pending.append(chunk)

    def work(chunk: Chunk) -> Extraction:
        last_error: Exception | None = None
        for _ in range(max_retries + 1):
            try:
                response = llm.complete(build_extraction_messages(chunk), json_mode=True, max_tokens=1200)
                return parse_extraction(response.text, chunk.chunk_id)
            except Exception as exc:  # noqa: BLE001 - 逐片容错是刻意的
                last_error = exc
        if progress:
            progress(f"抽取失败 {chunk.chunk_id}: {last_error}")
        return Extraction(chunk_id=chunk.chunk_id)

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for extraction in pool.map(work, pending):
                results[extraction.chunk_id] = extraction

    if cache:
        for chunk in chunks:
            extraction = results.get(chunk.chunk_id)
            if extraction is not None:
                cache.put(chunk, extraction)
        cache.save()
    return results


@dataclass
class KnowledgeGraph:
    """归并后的知识图谱。节点带别名与出处，边带支持数与出处。"""

    entities: dict[str, Entity] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    adjacency: dict[str, set[str]] = field(default_factory=dict)
    chunk_entities: dict[str, set[str]] = field(default_factory=dict)

    # ---- 构建 -----------------------------------------------------------
    @classmethod
    def from_extractions(cls, extractions: Iterable[Extraction]) -> KnowledgeGraph:
        extractions = list(extractions)
        # 1) 先收集 别名 -> 出现次数，用于选规范名
        alias_counts: dict[str, dict[str, int]] = {}
        for extraction in extractions:
            for item in extraction.entities:
                name = normalize_name(item.get("name", ""))
                if not name:
                    continue
                variants = {name} | {normalize_name(alias) for alias in (item.get("aliases") or []) if alias}
                for variant in variants:
                    if variant:
                        alias_counts.setdefault(variant, {})[name] = (
                            alias_counts.get(variant, {}).get(name, 0) + 1
                        )

        alias_to_canonical: dict[str, str] = {}
        for alias, votes in alias_counts.items():
            # 票数最多者胜；平票取更短的（通常是全称的简称）
            canonical = sorted(votes.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
            alias_to_canonical[alias] = canonical
        # 传递闭包：把指向别名的规范名也收敛到同一节点
        for alias, canonical in list(alias_to_canonical.items()):
            seen = {alias}
            target = canonical
            while alias_to_canonical.get(target, target) != target and target not in seen:
                seen.add(target)
                target = alias_to_canonical[target]
            alias_to_canonical[alias] = target

        graph = cls()
        type_votes: dict[str, dict[str, int]] = {}
        alias_sets: dict[str, set[str]] = {}

        for extraction in extractions:
            for item in extraction.entities:
                raw_name = normalize_name(item.get("name", ""))
                if not raw_name:
                    continue
                canonical = alias_to_canonical.get(raw_name, raw_name)
                entity = graph.entities.get(canonical)
                if entity is None:
                    entity = Entity(name=canonical, type=_normalize_type(item.get("type", "制度")))
                    graph.entities[canonical] = entity
                aliases = {raw_name} | {
                    normalize_name(alias) for alias in (item.get("aliases") or []) if alias
                }
                alias_sets.setdefault(canonical, set()).update(a for a in aliases if a)
                if extraction.chunk_id not in entity.chunk_ids:
                    entity.chunk_ids.append(extraction.chunk_id)
                graph.chunk_entities.setdefault(extraction.chunk_id, set()).add(canonical)
                votes = type_votes.setdefault(canonical, {})
                etype = _normalize_type(item.get("type", "制度"))
                votes[etype] = votes.get(etype, 0) + 1

        for canonical, entity in graph.entities.items():
            entity.type = sorted(type_votes[canonical].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            entity.aliases = sorted(alias_sets.get(canonical, set()) - {canonical})

        # 2) 归并关系（去自环；同 (s,p,t) 合并出处）
        merged: dict[tuple[str, str, str], list[str]] = {}
        for extraction in extractions:
            for item in extraction.relations:
                source = alias_to_canonical.get(normalize_name(item.get("source", "")), "")
                target = alias_to_canonical.get(normalize_name(item.get("target", "")), "")
                predicate = str(item.get("predicate", "")).strip()
                if not source or not target or source == target or not predicate:
                    continue
                if source not in graph.entities or target not in graph.entities:
                    continue
                key = (source, predicate, target)
                chunk_ids = merged.setdefault(key, [])
                if extraction.chunk_id not in chunk_ids:
                    chunk_ids.append(extraction.chunk_id)

        for (source, predicate, target), chunk_ids in sorted(merged.items()):
            graph.relations.append(
                Relation(source=source, target=target, predicate=predicate, chunk_ids=sorted(chunk_ids))
            )
            graph.adjacency.setdefault(source, set()).add(target)
            graph.adjacency.setdefault(target, set()).add(source)
        for name in graph.entities:
            graph.adjacency.setdefault(name, set())
        return graph

    # ---- 查询 -----------------------------------------------------------
    def search_names(self, text: str) -> list[str]:
        """把查询里出现的实体名捞出来（按名字长度降序，避免「Nebula」抢先于「Nebula 平台」）。"""
        normalized = normalize_name(text)
        hits: list[tuple[int, str]] = []
        for name, entity in self.entities.items():
            for variant in [name, *entity.aliases]:
                if len(variant) < 2:
                    continue
                if variant in normalized:
                    hits.append((len(variant), name))
                    break
        hits.sort(key=lambda item: (-item[0], item[1]))
        seen: list[str] = []
        for _, name in hits:
            if name not in seen:
                seen.append(name)
        return seen

    def expand(self, seeds: Sequence[str], hops: int = 2) -> dict[str, int]:
        """BFS 展开，返回 实体 -> 距种子最短跳数。"""
        distance: dict[str, int] = {}
        frontier = [seed for seed in seeds if seed in self.entities]
        for seed in frontier:
            distance[seed] = 0
        for depth in range(1, max(hops, 1) + 1):
            next_frontier: list[str] = []
            for node in frontier:
                for neighbor in sorted(self.adjacency.get(node, set())):
                    if neighbor not in distance:
                        distance[neighbor] = depth
                        next_frontier.append(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        return distance

    def chunks_for_weighted_entities(self, weights: dict[str, float]) -> dict[str, float]:
        """chunk -> 得分：实体权重按其出处累加（权重里已经含了距离衰减）。"""
        scores: dict[str, float] = {}
        for entity, weight in weights.items():
            entity_obj = self.entities.get(entity)
            if entity_obj is None:
                continue
            for chunk_id in entity_obj.chunk_ids:
                scores[chunk_id] = scores.get(chunk_id, 0.0) + weight
        return scores

    def chunks_for_entities(self, distance: dict[str, int]) -> dict[str, float]:
        """chunk -> 得分：越近的实体、被越多近邻实体提及，得分越高。"""
        return self.chunks_for_weighted_entities(
            {entity: 1.0 / (1.0 + depth) for entity, depth in distance.items()}
        )

    def relation_paths(self, source: str, target: str, max_hops: int = 3) -> list[list[str]]:
        """找出两个实体之间的短路径（用于解释「答案为什么来自这几篇」）。"""
        paths: list[list[str]] = []
        stack: list[tuple[str, list[str]]] = [(source, [source])]
        while stack:
            node, path = stack.pop(0)
            if len(path) > max_hops + 1:
                continue
            for neighbor in sorted(self.adjacency.get(node, set())):
                if neighbor in path:
                    continue
                if neighbor == target:
                    paths.append([*path, neighbor])
                else:
                    stack.append((neighbor, [*path, neighbor]))
        return paths[:5]

    def stats(self) -> dict[str, int]:
        by_type: dict[str, int] = {}
        for entity in self.entities.values():
            by_type[entity.type] = by_type.get(entity.type, 0) + 1
        return {
            "entities": len(self.entities),
            "relations": len(self.relations),
            "chunks_with_entities": len(self.chunk_entities),
            "isolated_entities": sum(1 for node, nbrs in self.adjacency.items() if not nbrs),
            **{f"type_{key}": value for key, value in sorted(by_type.items())},
        }

    # ---- 持久化 ---------------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {
            "entities": [entity.to_dict() for entity in self.entities.values()],
            "relations": [relation.to_dict() for relation in self.relations],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> KnowledgeGraph:
        payload = json.loads(path.read_text(encoding="utf-8"))
        graph = cls()
        for raw in payload["entities"]:
            entity = Entity(**raw)
            graph.entities[entity.name] = entity
        for raw in payload["relations"]:
            relation = Relation(**raw)
            graph.relations.append(relation)
            graph.adjacency.setdefault(relation.source, set()).add(relation.target)
            graph.adjacency.setdefault(relation.target, set()).add(relation.source)
        for name, entity in graph.entities.items():
            graph.adjacency.setdefault(name, set())
            for chunk_id in entity.chunk_ids:
                graph.chunk_entities.setdefault(chunk_id, set()).add(name)
        return graph


def link_entities_by_lexical(query: str, graph: KnowledgeGraph, top_k: int = 3) -> list[str]:
    """兜底实体链接：查询里没有字面命中时，用实体的令牌重合度匹配一次。"""
    names = [name for name in graph.entities if len(name) >= 2]
    if not names:
        return []
    query_tokens = set(tokenize(query))
    scored: list[tuple[float, str]] = []
    for name in names:
        name_tokens = set(tokenize(name))
        if not name_tokens:
            continue
        overlap = len(query_tokens & name_tokens) / len(name_tokens)
        if overlap:
            scored.append((overlap, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:top_k]]
