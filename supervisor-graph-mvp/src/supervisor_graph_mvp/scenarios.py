"""Scenario loading: the packaged example requests, plus ad-hoc requests from files.

A scenario is the MVP's input format — a request, the asset it is about, and the
evidence the requester claims to have. Keeping the examples packaged (rather than in
a test fixture) is what lets ``--fake`` replay a real trajectory with no key, no
network and no arguments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agents.base import AgentError

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"


def load_scenarios(data_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """All packaged scenarios, in file order."""
    path = Path(data_dir or DEFAULT_DATA_DIR) / "scenarios.json"
    if not path.is_file():
        raise AgentError("missing scenarios file: {}".format(path))
    document = json.loads(path.read_text(encoding="utf-8"))
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list):
        raise AgentError("scenarios.json must contain a 'scenarios' list")
    return [dict(item) for item in scenarios if isinstance(item, dict)]


def scenario_ids(data_dir: Optional[Path] = None) -> List[str]:
    return [str(item.get("id")) for item in load_scenarios(data_dir)]


def find_scenario(name: str, data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Look a scenario up by id (or by a unique substring of it)."""
    scenarios = load_scenarios(data_dir)
    for scenario in scenarios:
        if str(scenario.get("id")) == name:
            return scenario
    matches = [scenario for scenario in scenarios if name and name in str(scenario.get("id"))]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AgentError(
            "no scenario matches {!r}; packaged scenarios: {}".format(
                name, ", ".join(scenario_ids(data_dir))
            )
        )
    raise AgentError("scenario name {!r} is ambiguous".format(name))


def load_request(path: Path) -> Dict[str, Any]:
    """Read an ad-hoc request JSON file (same shape as one entry of scenarios.json)."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AgentError("request file must contain a JSON object")
    return document


__all__ = [
    "DEFAULT_DATA_DIR",
    "find_scenario",
    "load_request",
    "load_scenarios",
    "scenario_ids",
]
