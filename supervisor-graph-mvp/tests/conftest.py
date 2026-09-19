"""Shared fixtures for the offline suite.

Everything here is offline by construction: no network, no clock dependency, no real
API key. The scripted model client stands in for DeepSeek, the sandbox fixture copies
the packaged data so a test may mutate an asset, and the scenario fixtures reuse the
same scripted scripts the ``--fake`` CLI path replays.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from supervisor_graph_mvp.agents.registry import AgentRegistry, default_registry
from supervisor_graph_mvp.demo import load_script
from supervisor_graph_mvp.llm import ScriptedClient
from supervisor_graph_mvp.pipeline import Orchestrator
from supervisor_graph_mvp.scenarios import DEFAULT_DATA_DIR, find_scenario
from supervisor_graph_mvp.state import new_state


@pytest.fixture
def data_dir() -> Path:
    return DEFAULT_DATA_DIR


@pytest.fixture
def registry(data_dir: Path) -> AgentRegistry:
    return default_registry(data_dir)


@pytest.fixture
def sandbox(tmp_path: Path, data_dir: Path) -> Path:
    """A writable copy of ``data/`` so tests can mutate assets without touching the repo."""
    target = tmp_path / "data"
    shutil.copytree(data_dir, target)
    return target


@pytest.fixture
def scenario():
    """Load a packaged scenario by id."""

    def load(name: str) -> Dict[str, Any]:
        return find_scenario(name)

    return load


@pytest.fixture
def request_factory(sandbox: Path):
    """Build a minimal request against the sandboxed data dir."""

    def build(
        *,
        text: str = "请裁决这次外发",
        asset: str = "finance-export-2026-0919.csv",
        channel: str = "webmail",
        destination_type: str = "external",
        evidence: Optional[List[Dict[str, Any]]] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": extra.pop("id", "test-request"),
            "text": text,
            "asset": {
                "path": asset,
                "kind": extra.pop("kind", "表格"),
                "channel": channel,
                "destination": extra.pop("destination", "外部邮箱"),
                "destination_type": destination_type,
                "owner": extra.pop("owner", "测试属主"),
            },
            "evidence": evidence if evidence is not None else [],
        }
        payload.update(extra)
        return payload

    return build


@pytest.fixture
def scripted_orchestrator(sandbox: Path):
    """An orchestrator driven by a scripted model, over the sandboxed data dir."""

    def build(
        script: List[Any],
        *,
        max_repairs: int = 1,
        max_supersteps: int = 24,
        tracer: Optional[Any] = None,
        registry: Optional[AgentRegistry] = None,
    ) -> Orchestrator:
        model = ScriptedClient(script, label="fixture")
        return Orchestrator(
            model,
            registry or default_registry(sandbox),
            max_repairs=max_repairs,
            max_supersteps=max_supersteps,
            tracer=tracer,
        )

    return build


@pytest.fixture
def run_scenario(sandbox: Path):
    """Run one packaged scenario end to end with its scripted model."""

    def run(name: str, *, max_repairs: int = 1) -> Dict[str, Any]:
        model = ScriptedClient(load_script(name), label=name)
        orchestrator = Orchestrator(model, default_registry(sandbox), max_repairs=max_repairs)
        return orchestrator.run(find_scenario(name))

    return run


@pytest.fixture
def state_factory(sandbox: Path):
    """A bare state dict for unit-testing the pure helpers."""

    def build(request: Optional[Dict[str, Any]] = None, **extra: Any) -> Dict[str, Any]:
        run_state = new_state(request or {"id": "unit", "asset": {"path": "x.csv"}, "evidence": []})
        run_state.update(extra)
        return run_state

    return build


def write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
