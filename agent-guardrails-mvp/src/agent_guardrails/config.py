"""运行配置与产物路径 —— 只做装配，不含策略。

三条硬规则：

* **密钥只来自环境变量。** 没有配置文件、没有 keychain 回退、没有默认值。
* **密钥不进日志。** ``api_key`` 字段 ``repr=False``；``masked_key()`` 最多暴露后 4 位。
* **产物按运行隔离。** 每次运行一个时间戳目录（``runs/20260916_1935_live``），
  不覆盖历史证据 —— 这是后续对比"护栏改动前后"的前提。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_MODEL = "DEEPSEEK_MODEL"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 3


class ConfigError(RuntimeError):
    """无法组装出一次可运行的配置。"""


@dataclass(frozen=True)
class Config:
    """一次运行需要的传输参数（凭证 + 端点 + 模型 + 超时预算）。"""

    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES

    def endpoint(self, path: str) -> str:
        return "{}{}".format(self.base_url.rstrip("/"), path)

    @property
    def chat_completions_url(self) -> str:
        return self.endpoint("/chat/completions")

    @property
    def models_url(self) -> str:
        return self.endpoint("/models")

    def masked_key(self) -> str:
        """日志里可以安全出现的密钥表示。"""
        return "****" if len(self.api_key) <= 4 else "****{}".format(self.api_key[-4:])

    @classmethod
    def from_env(
        cls,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_retries: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> "Config":
        """显式参数 > 环境变量 > 默认值；密钥缺失是唯一的硬失败。"""
        source = os.environ if env is None else env
        resolved_key = (api_key or source.get(ENV_API_KEY, "")).strip()
        if not resolved_key:
            raise ConfigError(
                "no API key found; export {0}=sk-... (optionally {1}, {2})".format(
                    ENV_API_KEY, ENV_BASE_URL, ENV_MODEL
                )
            )
        return cls(
            api_key=resolved_key,
            base_url=(base_url or source.get(ENV_BASE_URL) or DEFAULT_BASE_URL).strip(),
            model=(model or source.get(ENV_MODEL) or DEFAULT_MODEL).strip(),
            timeout_s=DEFAULT_TIMEOUT_S if timeout_s is None else float(timeout_s),
            max_retries=DEFAULT_MAX_RETRIES if max_retries is None else int(max_retries),
        )


@dataclass(frozen=True)
class RunPaths:
    """一次运行的产物目录布局（目录即证据边界）。"""

    root: Path
    scenario: str

    @classmethod
    def fresh(cls, base: Path, mode: str, when: Optional[datetime] = None) -> "RunPaths":
        stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
        return cls(root=Path(base) / "{}_{}".format(stamp, mode), scenario="-")

    def for_scenario(self, scenario: str) -> "RunPaths":
        return RunPaths(root=self.root, scenario=scenario)

    @property
    def dir(self) -> Path:
        return self.root / self.scenario

    @property
    def log(self) -> Path:
        return self.dir / "agent.jsonl"

    @property
    def audit(self) -> Path:
        return self.dir / "audit.jsonl"

    @property
    def wire(self) -> Path:
        return self.dir / "wire.jsonl"

    @property
    def result(self) -> Path:
        return self.dir / "result.json"

    def ensure(self) -> "RunPaths":
        self.dir.mkdir(parents=True, exist_ok=True)
        return self
