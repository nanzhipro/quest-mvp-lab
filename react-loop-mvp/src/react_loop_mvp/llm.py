"""The only network surface of the MVP: one POST to an OpenAI-compatible endpoint.

DeepSeek speaks the OpenAI chat-completions protocol, so this client is a
few dozen lines of ``urllib`` instead of an SDK: build the payload, POST it,
normalise the reply. Two implementations of the same tiny protocol are provided:

``DeepSeekClient``
    The real client: retries transient failures with exponential backoff and
    records every request/response pair for evidence.
``ScriptedClient``
    A deterministic fake that replays a fixed list of replies. It is what makes
    the loop testable *and* what powers the offline demo (no key required).

Both satisfy :class:`ChatModel`, which is the only thing the loops know about.
"""

from __future__ import annotations

import json
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from .config import Config

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
RETRYABLE_EXCEPTIONS = (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)

Message = Dict[str, Any]


class LLMError(RuntimeError):
    """No usable reply could be obtained (auth, protocol, malformed body, timeout)."""


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by the model (native function-calling protocol)."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    arguments_raw: str = ""
    call_id: str = ""


@dataclass(frozen=True)
class Completion:
    """One model reply, normalised across providers and protocols."""

    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    finish_reason: str = ""

    @property
    def tokens(self) -> int:
        return int(self.usage.get("completion_tokens", 0))


class ChatModel(Protocol):
    """What a loop needs from a model: one round trip, nothing else."""

    def complete(
        self,
        messages: Sequence[Message],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Completion: ...

    @property
    def exchanges(self) -> List[Dict[str, Any]]:
        """Raw request/response pairs, in order — evidence for the trace file."""
        ...


def _usage_of(payload: Dict[str, Any]) -> Dict[str, int]:
    usage = payload.get("usage") or {}
    return {str(k): int(v) for k, v in usage.items() if isinstance(v, (int, float))}


def _parse_tool_calls(raw_calls: Sequence[Dict[str, Any]]) -> List[ToolCall]:
    calls: List[ToolCall] = []
    for raw in raw_calls:
        function = raw.get("function") or {}
        arguments_raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_raw) if arguments_raw.strip() else {}
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(
                name=str(function.get("name") or ""),
                arguments=arguments,
                arguments_raw=arguments_raw,
                call_id=str(raw.get("id") or ""),
            )
        )
    return calls


def _normalise(payload: Dict[str, Any], latency_ms: int) -> Completion:
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError("response carries no choices: {}".format(json.dumps(payload)[:400]))
    message = choices[0].get("message") or {}
    return Completion(
        text=message.get("content") or "",
        tool_calls=_parse_tool_calls(message.get("tool_calls") or []),
        reasoning=message.get("reasoning_content") or "",
        usage=_usage_of(payload),
        latency_ms=latency_ms,
        finish_reason=str(choices[0].get("finish_reason") or ""),
    )


class DeepSeekClient:
    """OpenAI-compatible chat client with retries, backoff and full wire capture."""

    def __init__(
        self,
        config: Config,
        *,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        sleep: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.config = config
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._exchanges: List[Dict[str, Any]] = []

    # ── public API ────────────────────────────────────────────────────────────
    @property
    def exchanges(self) -> List[Dict[str, Any]]:
        return list(self._exchanges)

    def list_models(self) -> List[str]:
        """``GET /models`` — informational only; gateways may not implement it."""
        request = urllib.request.Request(
            self.config.models_url,
            headers=self._headers(),
            method="GET",
        )
        payload = self._send(request, {"method": "GET", "path": "/models"})
        data = payload.get("data") or []
        return [str(item.get("id")) for item in data if isinstance(item, dict)]

    def complete(
        self,
        messages: Sequence[Message],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
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
        started = time.monotonic()
        payload = self._send(request, {"method": "POST", "path": "/chat/completions", "body": body})
        return _normalise(payload, int((time.monotonic() - started) * 1000))

    # ── internals ─────────────────────────────────────────────────────────────
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": "Bearer {}".format(self.config.api_key),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "react-loop-mvp/0.1",
        }

    def _send(self, request: urllib.request.Request, meta: Dict[str, Any]) -> Dict[str, Any]:
        """POST/GET with bounded retries on transient failures.

        Retries: only transport errors and 408/429/5xx. A 4xx (bad key, bad model,
        bad request) is a *user* error — retrying it would just burn the budget.
        """
        attempts = max(1, self.config.max_retries)
        last_error: Optional[str] = None
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                latency_ms = int((time.monotonic() - started) * 1000)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    self._record(
                        meta, attempt, None, raw, latency_ms, "malformed JSON: {}".format(exc)
                    )
                    raise LLMError("non-JSON response body: {}".format(raw[:300])) from exc
                self._record(meta, attempt, getattr(response, "status", 200), raw, latency_ms, None)
                return payload
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                latency_ms = int((time.monotonic() - started) * 1000)
                last_error = "HTTP {}: {}".format(exc.code, raw[:300])
                self._record(meta, attempt, exc.code, raw, latency_ms, last_error)
                if exc.code not in RETRYABLE_STATUS:
                    raise LLMError(last_error) from exc
            except RETRYABLE_EXCEPTIONS as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                last_error = "{}: {}".format(type(exc).__name__, exc)
                self._record(meta, attempt, None, "", latency_ms, last_error)

            if attempt < attempts:
                self._sleep(self._backoff(attempt))
        raise LLMError("giving up after {} attempt(s): {}".format(attempts, last_error))

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped so a demo never appears hung."""
        return min(8.0, 0.5 * (2 ** (attempt - 1))) + self._random_uniform(0.0, 0.25)

    def _record(
        self,
        meta: Dict[str, Any],
        attempt: int,
        status: Optional[int],
        body: str,
        latency_ms: int,
        error: Optional[str],
    ) -> None:
        self._exchanges.append(
            {
                "attempt": attempt,
                "status": status,
                "latency_ms": latency_ms,
                "error": error,
                "request": meta,
                "response_raw": body,
                "endpoint": self.config.chat_completions_url
                if meta.get("path") == "/chat/completions"
                else self.config.models_url,
            }
        )


class ScriptedClient:
    """Deterministic fake model: replays a fixed list of replies, one per call.

    Each script entry is either a plain string (assistant content) or a mapping
    with ``content`` / ``tool_calls`` / ``reasoning`` / ``usage``. Running past
    the end of the script raises :class:`LLMError` rather than inventing output,
    so an offline demo can never silently "succeed" for the wrong reason.
    """

    def __init__(self, script: Sequence[Any], *, label: str = "scripted") -> None:
        self._script = list(script)
        self.label = label
        self._calls: List[Dict[str, Any]] = []
        self._exchanges: List[Dict[str, Any]] = []

    @property
    def exchanges(self) -> List[Dict[str, Any]]:
        return list(self._exchanges)

    @property
    def calls(self) -> List[Dict[str, Any]]:
        """Every call recorded: index, messages, and whether tools were offered."""
        return [dict(call) for call in self._calls]

    @property
    def prompts(self) -> List[List[Message]]:
        """Every message list this fake was called with (asserting prompt growth)."""
        return [list(call["messages"]) for call in self._calls]

    @property
    def remaining(self) -> int:
        return max(0, len(self._script) - len(self._calls))

    def complete(
        self,
        messages: Sequence[Message],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Completion:
        index = len(self._calls)
        self._calls.append(
            {"index": index, "messages": [dict(m) for m in messages], "tools": tools is not None}
        )
        if index >= len(self._script):
            raise LLMError("{} script exhausted after {} call(s)".format(self.label, index))
        entry = self._script[index]
        if isinstance(entry, str):
            completion = Completion(text=entry)
        else:
            completion = Completion(
                text=entry.get("content", ""),
                tool_calls=_parse_tool_calls(
                    [
                        {"id": call.get("id", ""), "function": call}
                        for call in (entry.get("tool_calls") or [])
                    ]
                ),
                reasoning=entry.get("reasoning", ""),
                usage=entry.get("usage", {}),
                latency_ms=0,
                finish_reason=entry.get("finish_reason", "stop"),
            )
        self._exchanges.append(
            {
                "attempt": 1,
                "status": 200,
                "latency_ms": 0,
                "error": None,
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
                "endpoint": "scripted://{}".format(self.label),
            }
        )
        return completion
