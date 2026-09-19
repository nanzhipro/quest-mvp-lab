"""模型层：唯一的外部网络面（一次 POST），以及它的确定性替身。

* :class:`DeepSeekClient` —— 真实客户端。OpenAI 兼容协议 + 原生 function calling，
  用 ``urllib`` 而不是 SDK；瞬时故障指数退避重试（408/429/5xx 与网络错误），
  4xx 立即失败（密钥/模型/请求错误，重试只是烧钱）。
* :class:`ScriptedClient` —— 脚本化替身。它让"模型已被注入完全劫持"成为一种**可复现的输入**：
  离线测试与演练都不需要密钥，且能保证每次跑的都是同一条恶意轨迹。

两者都满足 :class:`ChatModel`：循环只需要"一次往返"，其余与普通代码无异。
"""

from __future__ import annotations

import json
import logging
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from . import obs
from .config import Config

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
RETRYABLE_EXCEPTIONS = (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)

Message = Dict[str, Any]


class LLMError(RuntimeError):
    """拿不到可用回复（鉴权、协议、畸形响应、超时）。"""


@dataclass(frozen=True)
class ToolCall:
    """模型请求的一次工具调用。"""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    arguments_raw: str = ""
    call_id: str = ""


@dataclass(frozen=True)
class Completion:
    """一次模型往返的归一化结果。"""

    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    finish_reason: str = ""

    @property
    def prompt_tokens(self) -> int:
        return int(self.usage.get("prompt_tokens", 0))

    @property
    def completion_tokens(self) -> int:
        return int(self.usage.get("completion_tokens", 0))

    @property
    def tool_names(self) -> List[str]:
        return [call.name for call in self.tool_calls]


class ChatModel(Protocol):
    """循环所需的全部模型能力：一次往返 + 证据留存。"""

    def complete(
        self, messages: Sequence[Message], tools: Optional[Sequence[Dict[str, Any]]] = None
    ) -> Completion: ...

    @property
    def exchanges(self) -> List[Dict[str, Any]]: ...


def _parse_tool_calls(raw_calls: Sequence[Dict[str, Any]]) -> List[ToolCall]:
    calls: List[ToolCall] = []
    for raw in raw_calls:
        function = raw.get("function") or {}
        arguments_raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_raw) if str(arguments_raw).strip() else {}
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(
                name=str(function.get("name") or ""),
                arguments=arguments,
                arguments_raw=str(arguments_raw),
                call_id=str(raw.get("id") or ""),
            )
        )
    return calls


class _WireRecorder:
    """请求/响应原文留痕：模型说了什么是**证据**，不能只存在于内存里。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else None
        self.records: List[Dict[str, Any]] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def add(self, record: Dict[str, Any]) -> None:
        self.records.append(record)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


class DeepSeekClient:
    """OpenAI 兼容客户端：重试、退避、全量 wire 留痕。"""

    def __init__(
        self,
        config: Config,
        *,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        wire_path: Optional[Path] = None,
        sleep: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.config = config
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._wire = _WireRecorder(wire_path)

    @property
    def exchanges(self) -> List[Dict[str, Any]]:
        return list(self._wire.records)

    def list_models(self) -> List[str]:
        """``GET /models`` —— 只用于自检（连通性 + 可用模型名）。"""
        request = urllib.request.Request(self.config.models_url, headers=self._headers(), method="GET")
        payload = self._send(request, {"method": "GET", "path": "/models"})
        return [str(item.get("id")) for item in (payload.get("data") or []) if isinstance(item, dict)]

    def complete(
        self, messages: Sequence[Message], tools: Optional[Sequence[Dict[str, Any]]] = None
    ) -> Completion:
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "stream": False,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"

        request = urllib.request.Request(
            self.config.chat_completions_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        started = time.perf_counter()
        payload = self._send(request, {"method": "POST", "path": "/chat/completions", "body": body})
        latency_ms = int((time.perf_counter() - started) * 1000)
        completion = self._normalise(payload, latency_ms)
        self._log_exchange(completion, offered_tools=len(tools or []), message_count=len(messages))
        return completion

    # ── internals ─────────────────────────────────────────────────────────────
    def _headers(self) -> Dict[str, str]:
        # 请求头（含密钥）永不进日志：这里只构造，不记录。
        return {
            "Authorization": "Bearer {}".format(self.config.api_key),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "agent-guardrails-mvp/0.1",
        }

    def _normalise(self, payload: Dict[str, Any], latency_ms: int) -> Completion:
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError("响应没有 choices：{}".format(json.dumps(payload, ensure_ascii=False)[:300]))
        message = choices[0].get("message") or {}
        usage = {
            str(key): int(value)
            for key, value in (payload.get("usage") or {}).items()
            if isinstance(value, (int, float))
        }
        return Completion(
            text=message.get("content") or "",
            tool_calls=_parse_tool_calls(message.get("tool_calls") or []),
            reasoning=message.get("reasoning_content") or "",
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=str(choices[0].get("finish_reason") or ""),
        )

    def _log_exchange(self, completion: Completion, *, offered_tools: int, message_count: int) -> None:
        obs.event(
            "llm.exchange",
            "模型往返完成",
            level=logging.DEBUG,
            model=self.config.model,
            latency_ms=completion.latency_ms,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            messages_in=message_count,
            tools_offered=offered_tools,
            finish_reason=completion.finish_reason,
            tool_calls=completion.tool_names,
        )

    def _send(self, request: urllib.request.Request, meta: Dict[str, Any]) -> Dict[str, Any]:
        attempts = max(1, self.config.max_retries)
        last_error = ""
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    status = getattr(response, "status", 200)
                latency_ms = int((time.perf_counter() - started) * 1000)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    self._record(meta, attempt, status, raw, latency_ms, "malformed JSON")
                    raise LLMError("响应不是 JSON：{}".format(raw[:200])) from exc
                self._record(meta, attempt, status, raw, latency_ms, "")
                return payload
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                latency_ms = int((time.perf_counter() - started) * 1000)
                last_error = "HTTP {}: {}".format(exc.code, raw[:200])
                self._record(meta, attempt, exc.code, raw, latency_ms, last_error)
                if exc.code not in RETRYABLE_STATUS:
                    raise LLMError(last_error) from exc
            except RETRYABLE_EXCEPTIONS as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                last_error = "{}: {}".format(type(exc).__name__, exc)
                self._record(meta, attempt, None, "", latency_ms, last_error)
            if attempt < attempts:
                self._sleep(self._backoff(attempt))
                obs.event(
                    "llm.retry",
                    "请求失败，退避后重试",
                    level=logging.WARNING,
                    attempt=attempt,
                    of=attempts,
                    error=last_error,
                )
        raise LLMError("重试 {} 次仍失败：{}".format(attempts, last_error))

    def _backoff(self, attempt: int) -> float:
        """指数退避 + 抖动，并封顶 —— 演示不应该看起来像卡死。"""
        return min(8.0, 0.5 * (2 ** (attempt - 1))) + self._random_uniform(0.0, 0.25)

    def _record(
        self,
        meta: Dict[str, Any],
        attempt: int,
        status: Optional[int],
        body: str,
        latency_ms: int,
        error: str,
    ) -> None:
        self._wire.add(
            {
                "ts": time.time(),
                "attempt": attempt,
                "status": status,
                "latency_ms": latency_ms,
                "error": error,
                "request": meta,
                "response_raw": body,
            }
        )


class ScriptedClient:
    """确定性替身：按脚本逐条重放回复。

    脚本项可以是字符串（最终回答）或 ``{"content":..., "tool_calls":[{"name":..., "arguments":{...}}]}``。
    脚本用尽后抛错，而不是编一个回复 —— 离线演练绝不能"因为没东西可放"而假装成功。
    """

    def __init__(self, script: Sequence[Any], *, label: str = "scripted") -> None:
        self._script = list(script)
        self.label = label
        self._calls: List[Dict[str, Any]] = []
        self._wire = _WireRecorder()

    @property
    def exchanges(self) -> List[Dict[str, Any]]:
        return list(self._wire.records)

    @property
    def calls(self) -> List[Dict[str, Any]]:
        return [dict(call) for call in self._calls]

    @property
    def remaining(self) -> int:
        return max(0, len(self._script) - len(self._calls))

    def complete(
        self, messages: Sequence[Message], tools: Optional[Sequence[Dict[str, Any]]] = None
    ) -> Completion:
        index = len(self._calls)
        self._calls.append({"index": index, "messages": [dict(m) for m in messages]})
        if index >= len(self._script):
            raise LLMError("{} 脚本已用尽（第 {} 次调用）".format(self.label, index))
        entry = self._script[index]
        if isinstance(entry, str):
            completion = Completion(text=entry, finish_reason="stop")
        else:
            raw_calls = [
                {
                    "id": call.get("id", "call_{}".format(position)),
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                    },
                }
                for position, call in enumerate(entry.get("tool_calls") or [])
            ]
            completion = Completion(
                text=entry.get("content", ""),
                tool_calls=_parse_tool_calls(raw_calls),
                reasoning=entry.get("reasoning", ""),
                usage=entry.get("usage", {"prompt_tokens": 0, "completion_tokens": 0}),
                finish_reason=entry.get("finish_reason", "tool_calls" if raw_calls else "stop"),
            )
        self._wire.add(
            {
                "ts": time.time(),
                "attempt": 1,
                "status": 200,
                "latency_ms": 0,
                "error": "",
                "request": {"method": "POST", "path": "/chat/completions", "scripted": True},
                "response_raw": json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": completion.text,
                                    "tool_calls": [
                                        {
                                            "id": call.call_id,
                                            "function": {
                                                "name": call.name,
                                                "arguments": call.arguments_raw,
                                            },
                                        }
                                        for call in completion.tool_calls
                                    ],
                                },
                                "finish_reason": completion.finish_reason,
                            }
                        ],
                        "usage": completion.usage,
                    },
                    ensure_ascii=False,
                ),
            }
        )
        obs.event(
            "llm.exchange",
            "脚本化模型（离线重放）",
            level=logging.DEBUG,
            scripted=True,
            label=self.label,
            tool_calls=completion.tool_names,
        )
        return completion
