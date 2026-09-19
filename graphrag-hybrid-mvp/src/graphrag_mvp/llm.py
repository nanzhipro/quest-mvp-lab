"""LLM 调用层：一个协议，两种实现。

- `DeepSeekClient`：OpenAI 兼容 /chat/completions，标准库 urllib 实现（不引入 openai SDK），
  带重试、超时、JSON 模式、磁盘响应缓存（同一输入第二次调用为 0 成本、0 延迟，
  也让抽取阶段的重跑结果可复现）。
- `ScriptedLLM`：离线测试替身，按调用顺序/子串匹配返回预设回复，并记录全部请求。

上层（图谱抽取、社区摘要、回答生成）只依赖 `LLM` 协议，因此整套流程可在无网络下全绿。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

Message = dict[str, str]


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLM(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


def _cache_key(model: str, messages: Sequence[Message], json_mode: bool) -> str:
    payload = json.dumps(
        {"model": model, "messages": list(messages), "json": json_mode},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _DiskCache:
    """按内容寻址的单文件缓存；写盘用原子替换，避免中断留下半截 JSON。"""

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> LLMResponse | None:
        if not self.directory:
            return None
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return LLMResponse(
            text=data["text"],
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
        )

    def put(self, key: str, response: LLMResponse) -> None:
        if not self.directory:
            return
        path = self.directory / f"{key}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "text": response.text,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)


@dataclass
class DeepSeekClient:
    """DeepSeek（或任意 OpenAI 兼容端点）客户端。"""

    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout: float = 120.0
    max_retries: int = 3
    cache_dir: Path | None = None
    retry_sleep: float = 1.5
    stats: dict[str, int] = field(
        default_factory=lambda: {"calls": 0, "cache_hits": 0, "prompt_tokens": 0, "completion_tokens": 0}
    )

    def __post_init__(self) -> None:
        self._cache = _DiskCache(self.cache_dir)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        key = _cache_key(self.model, messages, json_mode)
        cached = self._cache.get(key)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return cached

        body: dict[str, object] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._post(body)
                self.stats["calls"] += 1
                self.stats["prompt_tokens"] += response.prompt_tokens
                self.stats["completion_tokens"] += response.completion_tokens
                self._cache.put(key, response)
                return response
            except (urllib.error.URLError, TimeoutError, LLMError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_sleep * attempt)
        raise LLMError(f"LLM 调用失败（{self.max_retries} 次重试后）: {last_error}")

    def _post(self, body: dict[str, object]) -> LLMResponse:
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as raw:
                payload = json.loads(raw.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 4xx/5xx 都带响应体，便于定位
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code in (400, 401, 402, 403):
                raise LLMError(f"HTTP {exc.code}: {detail}") from exc
            raise
        usage = payload.get("usage") or {}
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError(f"响应中没有 choices: {json.dumps(payload)[:400]}")
        text = (choices[0].get("message") or {}).get("content") or ""
        if not text.strip():
            raise LLMError("模型返回空内容（thinking 模型偶发：可调大 max_tokens）")
        return LLMResponse(
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )


@dataclass
class ScriptedLLM:
    """离线替身：按 (1) 子串匹配 (2) 顺序队列 依次返回预设回复。"""

    rules: list[tuple[str, str]] = field(default_factory=list)
    queue: list[str] = field(default_factory=list)
    default: str = '{"entities": [], "relations": []}'
    calls: list[dict[str, object]] = field(default_factory=list)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        prompt = "\n".join(message.get("content", "") for message in messages)
        self.calls.append({"prompt": prompt, "json_mode": json_mode, "temperature": temperature})
        for needle, reply in self.rules:
            if needle in prompt:
                return LLMResponse(text=reply)
        if self.queue:
            return LLMResponse(text=self.queue.pop(0))
        return LLMResponse(text=self.default)
