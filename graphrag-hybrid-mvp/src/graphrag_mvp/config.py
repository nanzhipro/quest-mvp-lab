"""配置装载：项目自带 .env 解析（不依赖 python-dotenv），保证 MVP 自包含。

优先级：真实环境变量 > .env 文件 > 代码默认值。
密钥只进内存，不进日志、不进产物。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

CHANNEL_NAMES = ("lexical", "dense", "graph", "community")
DEFAULT_WEIGHTS = {"lexical": 1.0, "dense": 1.0, "graph": 1.2, "community": 0.6}

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """解析 KEY=VALUE 形式的 .env，返回文件里的键值；不覆盖已存在的环境变量。"""
    path = path or PROJECT_ROOT / ".env"
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def parse_weights(spec: str) -> dict[str, float]:
    """解析 `lexical:1.0,dense:1.0,graph:1.2` 形式；非法片段直接报错，不静默忽略。"""
    weights = dict(DEFAULT_WEIGHTS)
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition(":")
        name = name.strip()
        if name not in CHANNEL_NAMES:
            raise ValueError(f"未知检索通道: {name!r}（可选: {', '.join(CHANNEL_NAMES)}）")
        try:
            weights[name] = float(value)
        except ValueError as exc:  # noqa: TRY003 - 直接暴露非法输入
            raise ValueError(f"权重不是数字: {part!r}") from exc
    return weights


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    # bge 中文模型的检索式前缀：只加在 query 上，文档侧不加（模型卡推荐用法）
    query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    top_k: int = 12
    graph_hops: int = 3
    # 每个通道在最终上下文里至少保留的命中数（见 fusion.py 的通道保底说明）
    channel_floor: int = 1
    lexical_mode: str = "bigram"
    workers: int = 6
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    artifacts_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "artifacts")
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".cache")
    corpus_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "corpus")
    eval_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "eval")
    runs_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "runs")

    @classmethod
    def load(cls, env_path: Path | None = None, **overrides: object) -> Settings:
        load_dotenv(env_path)
        env = os.environ
        settings = cls(
            api_key=env.get("DEEPSEEK_API_KEY", ""),
            base_url=env.get("DEEPSEEK_BASE_URL", cls.base_url).rstrip("/"),
            llm_model=env.get("GRAPHRAG_LLM_MODEL", cls.llm_model),
            embed_model=env.get("GRAPHRAG_EMBED_MODEL", cls.embed_model),
            top_k=int(env.get("GRAPHRAG_TOP_K", cls.top_k)),
            graph_hops=int(env.get("GRAPHRAG_GRAPH_HOPS", cls.graph_hops)),
            channel_floor=int(env.get("GRAPHRAG_CHANNEL_FLOOR", cls.channel_floor)),
            lexical_mode=env.get("GRAPHRAG_LEXICAL_MODE", cls.lexical_mode),
            workers=int(env.get("GRAPHRAG_WORKERS", cls.workers)),
            weights=parse_weights(env.get("GRAPHRAG_WEIGHTS", "") or "")
            if env.get("GRAPHRAG_WEIGHTS")
            else dict(DEFAULT_WEIGHTS),
        )
        for key, value in overrides.items():
            if value is not None and hasattr(settings, key):
                setattr(settings, key, value)
        return settings

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY：复制 .env.example 为 .env 并填入密钥（.env 已被 gitignore）"
            )
        return self.api_key
