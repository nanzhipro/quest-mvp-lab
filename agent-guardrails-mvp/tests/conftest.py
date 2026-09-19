"""共享夹具：内存沙箱、注册表、审计、以及"日志即证据"的读取工具。

测试不触网、不看时钟、不依赖真实密钥 —— 需要模型的地方一律用脚本化替身。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

from agent_guardrails import obs
from agent_guardrails.audit import AuditLog
from agent_guardrails.tools import ToolRegistry, Workspace, workspace_tools


@pytest.fixture
def workspace() -> Workspace:
    return Workspace.seeded()


@pytest.fixture
def registry(workspace: Workspace) -> ToolRegistry:
    return ToolRegistry(workspace_tools(workspace))


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    """把结构化日志指到文件，测试直接读 JSONL 断言事件。"""
    path = tmp_path / "agent.jsonl"
    obs.configure_logging(level="DEBUG", console=False, jsonl_path=path)
    yield path
    obs.configure_logging(level="INFO", console=False)


def read_events(path: Path) -> List[Dict[str, Any]]:
    """读回 JSONL 事件流（一行一个 JSON 事件）。"""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def events_named(path: Path, name: str) -> List[Dict[str, Any]]:
    return [event for event in read_events(path) if event["event"] == name]
