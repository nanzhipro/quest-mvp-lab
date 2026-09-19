"""Shared fixtures. The whole suite runs offline: no API key, no network, no clock."""

from __future__ import annotations

from typing import List

import pytest

from react_loop_mvp.llm import ScriptedClient
from react_loop_mvp.tools import KnowledgeDoc, ToolRegistry, default_registry, load_knowledge_base


@pytest.fixture()
def docs() -> List[KnowledgeDoc]:
    """A four-document corpus with a deliberate near-miss ranking problem."""
    return [
        KnowledgeDoc(
            id="alpha",
            title="alpha 目录级 AUTH_OPEN 策略",
            tags=["es", "muting"],
            text="alpha 用反向静音实现目录级拦截，规范见 alpha/SPEC.md 的 §9。",
        ),
        KnowledgeDoc(
            id="alpha-spec",
            title="alpha/SPEC.md §9 结论",
            tags=["spec"],
            text="§9 结论：反向静音在真机验证通过，三份实现判定一致。",
        ),
        KnowledgeDoc(
            id="beta",
            title="beta 进程级拒绝",
            tags=["es", "process"],
            text="beta 用 YAML 描述进程级策略，拒绝指定进程打开 PDF。",
        ),
        KnowledgeDoc(
            id="gamma",
            title="gamma 现金流",
            tags=["finance"],
            text="gamma 与终端安全无关，用于验证检索不会乱命中。",
        ),
    ]


@pytest.fixture()
def registry(docs: List[KnowledgeDoc]) -> ToolRegistry:
    return default_registry(docs)


@pytest.fixture()
def kb_registry() -> ToolRegistry:
    """The registry built on the KB shipped inside the package."""
    assert load_knowledge_base()
    return default_registry()


@pytest.fixture()
def multi_hop_script() -> List[object]:
    """Two tool calls then a final answer — the canonical ReAct trajectory."""
    return [
        "Thought: 先检索项目。\nAction: search_docs\nAction Input: 目录级 AUTH_OPEN\n",
        "Thought: 正文指向 SPEC.md，去取结论。\n"
        "Action: search_docs\n"
        "Action Input: SPEC.md §9 结论\n",
        "Thought: 事实齐了。\nFinal Answer: alpha，规范是 alpha/SPEC.md 的 §9。",
    ]


@pytest.fixture()
def scripted_factory():
    """Build a ScriptedClient from a list of raw replies."""

    def _build(replies, **kwargs) -> ScriptedClient:
        return ScriptedClient(replies, **kwargs)

    return _build
