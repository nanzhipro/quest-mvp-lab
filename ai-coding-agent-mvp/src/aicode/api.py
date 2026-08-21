"""Minimal OpenAI-compatible chat client built on stdlib urllib.

Works against any endpoint that speaks the OpenAI /chat/completions protocol:
Aliyun Bailian MaaS, OpenAI, vLLM, and similar gateways.

Design notes:

- **Streaming (SSE) by default.** Long generations (this model emits multi-KB
  reasoning traces) produce zero bytes for minutes in non-streaming mode, which
  reads as a hang and trips the socket timeout. SSE keeps data flowing and lets
  callers render progress. Tool-call deltas are accumulated and parsed exactly
  like non-streaming responses.
- **Non-streaming fallback.** If the endpoint rejects ``stream: true`` with a
  4xx, the request is retried once without streaming.
- **Retries.** Connection-level failures (timeouts, resets) and 5xx are retried
  with exponential backoff; 4xx (auth, bad request) fails fast. POST /chat is
  idempotent, so re-issuing is safe.
- **Normalization.** Both paths return the same shape: content / reasoning /
  parsed tool_calls / finish_reason / usage.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class APIError(RuntimeError):
    """Raised when the remote endpoint answers with a non-2xx status or the network fails."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__("API error HTTP {}: {}".format(status, body[:400]))
        self.status = status
        self.body = body


class ChatClient:
    """Thin wrapper over the OpenAI-compatible chat-completions protocol."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 300,
        max_tokens: int = 4096,
        temperature: Optional[float] = None,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    # ── low-level request helpers ──────────────────────────────────────────

    def _sleep(self, attempt: int) -> None:
        time.sleep(self.retry_backoff * attempt)

    def _build_request(
        self, path: str, payload: Optional[Dict], method: str
    ) -> urllib.request.Request:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Authorization": "Bearer " + self.api_key}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )

    def _network_message(self, err: OSError) -> str:
        if isinstance(err, TimeoutError):
            return "read timed out after {}s".format(self.timeout)
        return "network error: {}".format(err)

    def _request_json(self, method: str, path: str, payload: Optional[Dict] = None) -> Any:
        """JSON request with retry; returns the parsed response body."""
        last_err = ""
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(attempt)
            try:
                with urllib.request.urlopen(
                    self._build_request(path, payload, method), timeout=self.timeout
                ) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", "replace")
                if err.code in RETRYABLE_STATUS and attempt < self.max_retries:
                    last_err = "HTTP {}".format(err.code)
                    continue
                raise APIError(err.code, body) from err
            except OSError as err:
                last_err = self._network_message(err)
                if attempt >= self.max_retries:
                    message = "{} — {} retries exhausted".format(last_err, attempt)
                    raise APIError(0, message) from err
        raise APIError(0, "gave up after {} retries: {}".format(self.max_retries, last_err))

    # ── chat: streaming first, non-streaming fallback ──────────────────────

    def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        on_content: Optional[Callable[[str], None]] = None,
    ) -> Dict:
        """Send one completion round and return a normalized message.

        ``on_content``, when given, receives content text as it arrives from the
        stream — the hook for live progress display.
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        try:
            return self._chat_streaming(payload, on_content)
        except APIError as err:
            # Endpoints that don't speak streaming reject `stream: true` with 4xx.
            if err.status in (400, 404, 422) and "stream" in err.body.lower():
                payload["stream"] = False
                return self._chat_json(payload)
            raise

    # ── argument parsing ───────────────────────────────────────────────────

    @staticmethod
    def _parse_arguments(raw_args: Any) -> Dict:
        """Parse tool-call arguments defensively.

        Handles the three shapes seen in the wild: an already-parsed dict
        (some gateways), a JSON string (OpenAI spec), and malformed JSON
        (models emitting literal newlines inside string values) which is
        preserved under the ``_raw`` key for salvage at dispatch time.
        """
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
            except (ValueError, TypeError):
                return {"_raw": raw_args}
            return parsed if isinstance(parsed, dict) else {"_raw": raw_args}
        return {"_raw": raw_args}

    def _chat_streaming(self, payload: Dict, on_content: Optional[Callable[[str], None]]) -> Dict:
        last_err = ""
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(attempt)
            try:
                with urllib.request.urlopen(
                    self._build_request("/chat/completions", payload, "POST"), timeout=self.timeout
                ) as resp:
                    return self._parse_sse(resp, on_content)
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", "replace")
                if err.code in RETRYABLE_STATUS and attempt < self.max_retries:
                    last_err = "HTTP {}".format(err.code)
                    continue
                raise APIError(err.code, body) from err
            except OSError as err:
                last_err = self._network_message(err)
                if attempt >= self.max_retries:
                    message = "{} — {} retries exhausted".format(last_err, attempt)
                    raise APIError(0, message) from err
        raise APIError(0, "gave up after {} retries: {}".format(self.max_retries, last_err))

    def _chat_json(self, payload: Dict) -> Dict:
        """Non-streaming fallback path; same normalization as the SSE parser."""
        resp = self._request_json("POST", "/chat/completions", payload)
        return self._normalize(resp)

    @staticmethod
    def _parse_sse(resp: Any, on_content: Optional[Callable[[str], None]]) -> Dict:
        content: List[str] = []
        reasoning: List[str] = []
        tool_acc: Dict[int, Dict[str, str]] = {}
        finish = None
        usage: Dict = {}
        for raw in resp:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            if chunk.get("usage"):
                usage = chunk["usage"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                piece = delta["content"]
                content.append(piece)
                if on_content:
                    on_content(piece)
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                if isinstance(tc.get("id"), str):
                    acc["id"] += tc["id"]
                fn = tc.get("function") or {}
                if isinstance(fn.get("name"), str):
                    acc["name"] += fn["name"]
                arg_val = fn.get("arguments")
                if isinstance(arg_val, str):
                    acc["args"] += arg_val

        if not content and not tool_acc and not reasoning:
            raise APIError(0, "empty SSE stream")

        tool_calls = []
        for idx in sorted(tool_acc):
            acc = tool_acc[idx]
            tool_calls.append(
                {
                    "id": acc["id"],
                    "name": acc["name"],
                    "arguments": ChatClient._parse_arguments(acc["args"] or "{}"),
                }
            )
        return {
            "content": "".join(content),
            "reasoning": "".join(reasoning),
            "tool_calls": tool_calls,
            "finish_reason": finish,
            "usage": usage,
        }

    @staticmethod
    def _normalize(resp: Dict) -> Dict:
        """Normalize a non-streaming /chat/completions response body."""
        choices = resp.get("choices") or []
        if not choices:
            raise APIError(0, "empty choices in response: {}".format(json.dumps(resp)[:400]))
        msg = choices[0].get("message") or {}
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tool_calls.append(
                {
                    "id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "arguments": ChatClient._parse_arguments(fn.get("arguments") or "{}"),
                }
            )
        return {
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "tool_calls": tool_calls,
            "finish_reason": choices[0].get("finish_reason"),
            "usage": resp.get("usage") or {},
        }

    # ── misc ───────────────────────────────────────────────────────────────

    def list_models(self) -> List[str]:
        data = self._request_json("GET", "/models")
        return [m.get("id", "") for m in data.get("data", [])]
