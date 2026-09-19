"""可观测性：一条事件流，两种读者。

为什么这样设计（而不是让模块各自 ``print``）：

* **事件名而非自由文本。** 每个事件都有稳定的名字（``gate.decision`` / ``budget.trip`` /
  ``llm.exchange``），JSONL 可以按名字 grep、聚合、画图；控制台只是它的可读渲染。
* **上下文跟随执行流，而不是靠传参。** 会话/步骤/工具/场景放进 :mod:`contextvars`，
  任何一行日志都自带定位坐标 —— 排查"第 3 步那个被拒的调用是谁干的"时不需要翻代码。
* **标准库 ``logging``，零依赖。** 级别、传播、handler、第三方采集器（OTel 等）都沿用生态约定，
  不发明新的日志框架；日志写 stderr，stdout 留给数据。
* **密钥永不进日志。** 本模块只格式化调用方递进来的字段；``llm.py`` 只上报 method/path/model/
  tokens/延迟，从不上报请求头。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, TextIO, Tuple

LOGGER_NAME = "agent_guardrails"

_logger = logging.getLogger(LOGGER_NAME)

# 上下文维度：出现在每一条日志里，构成"这一行属于谁"的坐标。
_CONTEXT: Dict[str, ContextVar[Any]] = {
    "session": ContextVar("ag_session", default="-"),
    "scenario": ContextVar("ag_scenario", default="-"),
    "step": ContextVar("ag_step", default=0),
    "tool": ContextVar("ag_tool", default="-"),
}
CONTEXT_KEYS: Tuple[str, ...] = tuple(_CONTEXT)

_RESET, _DIM, _BOLD = "\033[0m", "\033[2m", "\033[1m"
_LEVEL_COLOR = {
    logging.DEBUG: "\033[2m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}

# 控制台上单个字段的渲染上限：JSONL 里保留全文，人眼看的这一路截断。
_MAX_CONSOLE_VALUE = 200


@contextmanager
def bind_context(**values: Any) -> Iterator[None]:
    """在当前执行流上绑定日志上下文（退出时自动还原，支持嵌套）。

    >>> with bind_context(session="a1b2", step=1):
    ...     event("demo.tick", step_value=1)
    """
    unknown = set(values) - set(_CONTEXT)
    if unknown:
        raise KeyError("unknown log context keys: {}".format(sorted(unknown)))
    tokens = [(var, var.set(values[key])) for key, var in _CONTEXT.items() if key in values]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current_context() -> Dict[str, Any]:
    """读取当前上下文（测试与结果落盘会用到）。"""
    return {key: var.get() for key, var in _CONTEXT.items()}


def event(name: str, message: str = "", *, level: int = logging.INFO, **fields: Any) -> None:
    """发射一条结构化事件。

    ``name`` 是稳定的事件名，``fields`` 是结构化负载；``message`` 是可选的补充说明。
    约定：可被程序消费的东西一律放 ``fields``，``message`` 只写给人看。
    """
    _logger.log(level, message or name, extra={"event": name, "fields": fields}, stacklevel=2)


class _ContextFilter(logging.Filter):
    """把上下文与事件字段注入每条记录，保证 formatter 拿到的是完整记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, var in _CONTEXT.items():
            if not hasattr(record, key):
                setattr(record, key, var.get())
        if not hasattr(record, "event"):
            record.event = "-"
        if not hasattr(record, "fields"):
            record.fields = {}
        return True


def _event_name(record: logging.LogRecord) -> str:
    """事件名走 ``extra`` 注入，类型系统看不到 —— 这里集中取值，避免散落的 getattr。"""
    return str(getattr(record, "event", "-"))


def _event_fields(record: logging.LogRecord) -> Dict[str, Any]:
    fields = getattr(record, "fields", None)
    return dict(fields) if isinstance(fields, dict) else {}


def _brief(value: Any, limit: int = _MAX_CONSOLE_VALUE) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ConsoleFormatter(logging.Formatter):
    """给人看的一路：时间 · 级别 · 上下文坐标 · 事件名 · 字段。"""

    def __init__(self, *, color: Optional[bool] = None) -> None:
        super().__init__()
        self.color = sys.stderr.isatty() and not os.environ.get("NO_COLOR") if color is None else color

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        clock = "{}.{:03d}".format(stamp, int(record.msecs))
        level = record.levelname
        if self.color:
            level = "{}{}{}".format(_LEVEL_COLOR.get(record.levelno, ""), level, _RESET)
        context = " ".join("{}={}".format(key, _brief(getattr(record, key), 32)) for key in CONTEXT_KEYS)
        line = "{clock} {level:<7} [{context}] {event:<26} {message}".format(
            clock=clock,
            level=level,
            context=context,
            event=_event_name(record),
            message=record.getMessage().strip(),
        ).rstrip()
        fields = _event_fields(record)
        if fields:
            rendered = " ".join("{}={}".format(key, _brief(value)) for key, value in sorted(fields.items()))
            line = "{}  │  {}".format(line, rendered)
        if record.exc_info:
            line = "{}\n{}".format(line, self.formatException(record.exc_info))
        return line


class JsonFormatter(logging.Formatter):
    """给机器看的一路：一行一个 JSON 事件，字段平铺，便于 grep / jq / 入库。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": _event_name(record),
            "message": record.getMessage(),
        }
        for key in CONTEXT_KEYS:
            payload[key] = getattr(record, key)
        payload.update(_event_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=False, default=str)


def configure_logging(
    *,
    level: str = "INFO",
    console: bool = True,
    jsonl_path: Optional[Path] = None,
    stream: Optional[TextIO] = None,
    color: Optional[bool] = None,
) -> logging.Logger:
    """装配日志出口（重复调用安全，先清空旧 handler）。

    Args:
        level: 全局级别名（``DEBUG`` 可看到每一步的 span 与原始字段）。
        console: 是否输出人到 stderr 的可读日志。
        jsonl_path: 结构化日志落盘路径；为 ``None`` 时只输出控制台。
    """
    logger = _logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    handlers: List[logging.Handler] = []

    if console:
        console_handler = logging.StreamHandler(stream or sys.stderr)
        console_handler.setFormatter(ConsoleFormatter(color=color))
        handlers.append(console_handler)

    if jsonl_path is not None:
        path = Path(jsonl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        handlers.append(file_handler)

    for handler in handlers:
        handler.addFilter(_ContextFilter())
        logger.addHandler(handler)

    if not handlers:
        logger.addHandler(logging.NullHandler())
    return logger
