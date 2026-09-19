"""Data-loading helpers shared by the specialist agents.

Every agent reads its rules from a JSON file under ``data/`` — the rules are data,
the engine is code, and the two are deliberately kept apart so a policy change is a
file diff, not a code review of an algorithm. Loaded documents are cached per path
because a run reads the same table several times (once per repair round).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .agents.base import AgentError

_CACHE: Dict[str, Any] = {}


def data_file(data_dir: Path, name: str) -> Path:
    path = Path(data_dir) / name
    if not path.is_file():
        raise AgentError("missing data file: {}".format(path))
    return path


def load_json(path: Path, *, cache: bool = True) -> Dict[str, Any]:
    """Read a JSON object, with an optional process-lifetime cache keyed by path+mtime."""
    resolved = Path(path)
    key = "{}::{}".format(resolved, resolved.stat().st_mtime_ns)
    if cache and key in _CACHE:
        return _CACHE[key]
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentError("{} is not valid JSON: {}".format(resolved, exc)) from exc
    if not isinstance(payload, dict):
        raise AgentError("{} must contain a JSON object".format(resolved))
    if cache:
        _CACHE[key] = payload
    return payload


def load_table(data_dir: Path, name: str, key: str) -> Dict[str, Any]:
    """Load a JSON object and return its named list field (``rules``, ``actions``, …)."""
    document = load_json(data_file(data_dir, name))
    rows = document.get(key)
    if not isinstance(rows, list):
        raise AgentError("{} must contain a list under {!r}".format(name, key))
    return document


def clear_cache() -> Optional[int]:
    """Drop the loader cache (tests use this after editing a fixture)."""
    count = len(_CACHE)
    _CACHE.clear()
    return count
