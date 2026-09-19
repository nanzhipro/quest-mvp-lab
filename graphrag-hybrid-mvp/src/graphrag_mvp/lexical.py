"""词法通道：自研 BM25 + 中英混排分词。

为什么不用 jieba / rank_bm25：
- 分词：中文用「单字 + 相邻双字」混合切分，不需要词典即可覆盖未登录词（系统名、编号），
  且与 BM25 的 IDF 机制天然契合；英文/数字按整词保留，编号（CR-2、180）不被拆散。
- BM25：不到 80 行，避免为一个公式引入依赖；参数 k1/b 可调便于消融实验。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .types import Chunk, RankedHit

_CJK = r"\u4e00-\u9fff"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[A-Za-z][A-Za-z0-9\-_.]*|\d+")
_STOPWORDS = frozenset(
    {
        "的",
        "了",
        "和",
        "与",
        "及",
        "或",
        "是",
        "在",
        "对",
        "为",
        "由",
        "按",
        "并",
        "等",
        "the",
        "a",
        "of",
        "and",
    }
)
_CJK_CHAR_RE = re.compile(rf"^[{_CJK}]$")
LEXICAL_MODES = ("bigram", "unigram_bigram")


def tokenize(text: str, mode: str = "bigram") -> list[str]:
    """中文切分策略可切换；英文/数字一律整词保留，编号（CR-2、180）不被拆散。

    - `bigram`（默认）：中文按相邻双字切分。这是 CJK 检索的经典做法（Lucene CJKBigramFilter 同款），
      不依赖词典即可覆盖未登录词，同时对单字噪声有天然抑制。
    - `unigram_bigram`：额外加入单字。召回更宽，但中文里几乎任何句子都会「命中」，
      BM25 的区分度随之下降 —— 消融时用它观察「词法通道过强会不会掩盖图谱通道的收益」。
    """
    if mode not in LEXICAL_MODES:
        raise ValueError(f"未知分词模式: {mode!r}（可选: {', '.join(LEXICAL_MODES)}）")
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        token = raw.lower()
        if _CJK_CHAR_RE.match(token):  # 单字
            if token not in _STOPWORDS:
                tokens.append(token)
        elif token[0].isascii():
            if token not in _STOPWORDS:
                tokens.append(token)
        else:  # 中文串
            chars = [ch for ch in token if ch not in _STOPWORDS]
            tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
            if mode == "unigram_bigram":
                tokens.extend(chars)
    return tokens


class BM25Index:
    """Okapi BM25，内存索引；语料规模在万级 chunk 内足够快。"""

    def __init__(
        self, chunks: list[Chunk], *, k1: float = 1.5, b: float = 0.75, mode: str = "bigram"
    ) -> None:
        self.chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self.order = [chunk.chunk_id for chunk in chunks]
        self.k1 = k1
        self.b = b
        self.mode = mode
        self._freqs: dict[str, Counter[str]] = {}
        self._lengths: dict[str, int] = {}
        self._df: dict[str, int] = defaultdict(int)
        for chunk in chunks:
            tokens = tokenize(chunk.text, mode)
            self._freqs[chunk.chunk_id] = Counter(tokens)
            self._lengths[chunk.chunk_id] = max(len(tokens), 1)
            for term in set(tokens):
                self._df[term] += 1
        self._avg_len = sum(self._lengths.values()) / max(len(self._lengths), 1)
        self._n = max(len(self._freqs), 1)

    def idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def score(self, chunk_id: str, query_tokens: list[str]) -> float:
        freqs = self._freqs[chunk_id]
        length = self._lengths[chunk_id]
        total = 0.0
        for term in query_tokens:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            norm = self.k1 * (1 - self.b + self.b * length / self._avg_len)
            total += self.idf(term) * (tf * (self.k1 + 1)) / (tf + norm)
        return total

    def search(self, query: str, top_k: int = 20) -> list[RankedHit]:
        """返回按分数降序的命中；分数为 0 的 chunk 直接丢弃（不制造假命中）。"""
        query_tokens = tokenize(query, self.mode)
        scored: list[tuple[float, str]] = []
        for chunk_id in self.order:
            value = self.score(chunk_id, query_tokens)
            if value > 0:
                scored.append((value, chunk_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            RankedHit(chunk_id=chunk_id, score=round(value, 6), rank=rank, channel="lexical")
            for rank, (value, chunk_id) in enumerate(scored[:top_k], start=1)
        ]
