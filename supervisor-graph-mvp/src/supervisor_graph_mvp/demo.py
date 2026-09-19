"""Offline replay: the scripted Supervisor that makes ``--fake`` possible without a key.

The script is *the same interface* the real client uses, which is the point — the
offline demo exercises the real graph, the real specialists, the real consistency
checker, and only replaces the three narrow model calls. Every scripted reply is a
plausible model reply, including the two that are deliberately wrong: the plan that
forgets a dependency, and the plan that forgets a specialist. Those two are what make
the repair cycle visible without needing to wait for a real model to slip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from .agents.base import AgentError
from .loader import data_file, load_json
from .scenarios import DEFAULT_DATA_DIR


def load_script(name: str, data_dir: Optional[Path] = None) -> List[Any]:
    """Read one scenario's scripted replies, as :class:`ScriptedClient` entries.

    Entries are either a plain string (the narrative) or an object with ``json``
    (a structured reply such as a plan) — kept as real JSON in the data file so the
    script stays readable and diffable.
    """
    document = load_json(data_file(Path(data_dir or DEFAULT_DATA_DIR), "demo_script.json"))
    scripts: Mapping[str, Any] = document.get("scripts") or {}
    if name not in scripts:
        raise AgentError(
            "no scripted run for {!r}; available: {}".format(name, ", ".join(sorted(scripts)))
        )
    entries: List[Any] = []
    for item in scripts[name]:
        if isinstance(item, str):
            entries.append(item)
            continue
        if not isinstance(item, Mapping):
            raise AgentError("script entry for {} must be a string or an object".format(name))
        usage = dict(item.get("usage") or {})
        if "json" in item:
            entries.append(
                {"content": json.dumps(item["json"], ensure_ascii=False), "usage": usage}
            )
        else:
            entries.append({"content": str(item.get("text") or ""), "usage": usage})
    return entries


def scripted_scenarios(data_dir: Optional[Path] = None) -> Sequence[str]:
    document = load_json(data_file(Path(data_dir or DEFAULT_DATA_DIR), "demo_script.json"))
    return tuple(sorted((document.get("scripts") or {}).keys()))


__all__ = ["load_script", "scripted_scenarios"]
