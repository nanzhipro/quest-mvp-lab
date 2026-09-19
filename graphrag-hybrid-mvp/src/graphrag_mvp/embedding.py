"""稠密通道：中文向量嵌入 + 余弦检索。

两个实现：
- `OnnxEmbedder`：fastembed + bge-small-zh-v1.5（ONNX/CPU，不需要 torch），生产路径。
- `HashingEmbedder`：字符 n-gram 哈希投影，零依赖、完全确定性 —— 离线测试与
  「模型不可用时的降级路径」都用它，保证测试不依赖网络与模型文件。

嵌入结果按 (模型名, chunk_id) 落盘缓存：重复构建索引不重复算，语料改动只补算新 chunk。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .types import Chunk, RankedHit


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return list(vector)
    return [value / norm for value in vector]


@dataclass
class HashingEmbedder:
    """字符 2/3-gram → 固定维度哈希投影，带符号散列降低碰撞偏置。"""

    dim: int = 512
    name: str = "hashing-char-ngram-512"

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        cleaned = " ".join(text.split())
        for n in (2, 3):
            for index in range(max(len(cleaned) - n + 1, 1)):
                gram = cleaned[index : index + n]
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[bucket] += sign
        return _l2_normalize(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


@dataclass
class OnnxEmbedder:
    """fastembed 封装；模型按需下载到 cache_dir（离线环境先手工放好模型即可复用）。"""

    model_name: str = "BAAI/bge-small-zh-v1.5"
    cache_dir: Path | None = None
    query_prefix: str = ""
    threads: int = 4
    _model: object | None = field(default=None, init=False, repr=False)
    name: str = field(default="", init=False)
    dim: int = field(default=512, init=False)

    def __post_init__(self) -> None:
        self.name = f"onnx:{self.model_name}"

    def _ensure(self) -> object:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - 环境缺失时的显式提示
                raise RuntimeError(
                    "未安装 fastembed：`uv sync` 或改用 --embedder hashing 走离线降级路径"
                ) from exc
            kwargs: dict[str, object] = {"model_name": self.model_name, "threads": self.threads}
            if self.cache_dir is not None:
                kwargs["cache_dir"] = str(self.cache_dir)
            self._model = TextEmbedding(**kwargs)
            self.dim = len(next(iter(self._model.embed(["维度探测"]))))  # type: ignore[attr-defined]
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._ensure()
        return [list(map(float, vector)) for vector in model.embed(list(texts))]  # type: ignore[attr-defined]

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure()
        prefixed = f"{self.query_prefix}{text}" if self.query_prefix else text
        vector = next(iter(model.query_embed([prefixed])))  # type: ignore[attr-defined]
        return [float(value) for value in vector]


@dataclass
class EmbeddingStore:
    """chunk_id -> 向量的落盘缓存。"""

    path: Path
    model: str = ""
    dim: int = 0
    vectors: dict[str, list[float]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> EmbeddingStore:
        store = cls(path=path)
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                header = json.loads(handle.readline())
                store.model = header.get("model", "")
                store.dim = int(header.get("dim", 0))
                for line in handle:
                    if line.strip():
                        record = json.loads(line)
                        store.vectors[record["chunk_id"]] = record["vector"]
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"model": self.model, "dim": self.dim}, ensure_ascii=False) + "\n")
            for chunk_id, vector in self.vectors.items():
                handle.write(json.dumps({"chunk_id": chunk_id, "vector": vector}, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def build(self, chunks: Sequence[Chunk], embedder: Embedder, *, batch_size: int = 32) -> int:
        """只补算缺失向量；模型名变化时整体重算。返回本次实际计算的条数。"""
        if self.model != embedder.name or (self.dim and self.dim != embedder.dim):
            self.vectors, self.model, self.dim = {}, embedder.name, embedder.dim
        pending = [chunk for chunk in chunks if chunk.chunk_id not in self.vectors]
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            for chunk, vector in zip(batch, embedder.embed_documents([c.text for c in batch]), strict=True):
                self.vectors[chunk.chunk_id] = vector
        self.model = embedder.name
        self.dim = embedder.dim or self.dim
        return len(pending)


class DenseIndex:
    """余弦检索：向量已归一化，点积即余弦相似度（不引入 numpy 之外的依赖）。"""

    def __init__(self, chunks: Sequence[Chunk], store: EmbeddingStore) -> None:
        self.chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self.order = [chunk.chunk_id for chunk in chunks if chunk.chunk_id in store.vectors]
        self._matrix: list[list[float]] = [store.vectors[chunk_id] for chunk_id in self.order]

    def search(self, query_vector: Sequence[float], top_k: int = 20) -> list[RankedHit]:
        scores: list[tuple[float, str]] = []
        for chunk_id, vector in zip(self.order, self._matrix, strict=True):
            score = sum(a * b for a, b in zip(query_vector, vector, strict=False))
            scores.append((score, chunk_id))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [
            RankedHit(chunk_id=chunk_id, score=round(score, 6), rank=rank, channel="dense")
            for rank, (score, chunk_id) in enumerate(scores[:top_k], start=1)
        ]
