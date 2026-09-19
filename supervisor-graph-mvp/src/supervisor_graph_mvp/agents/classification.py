"""Classification specialist — scans the asset and rolls detector hits into a level.

Deterministic by construction: the detectors are regexes in ``detectors.json``, the
level is the highest level among the detectors that fired, and the samples in the
output are masked (prefix/suffix only) so the ruling can be shown to a reviewer
without re-leaking the very data that made the file sensitive. No model is involved,
and the same file always yields the same level, hit count and citations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Pattern

from ..loader import data_file, load_json
from ..predicates import LEVEL_ORDER, highest_level
from .base import AgentContext, AgentError, Specialist, mask


class ClassificationAgent(Specialist):
    """Decides P1–P4 for the request's asset from the detector table."""

    name = "classification"
    mission = "资产敏感级别判定（按检测器命中，取最高级别）"
    requires: tuple = ()
    required_output_keys = ("level", "detectors")

    def _compiled(self, data_dir) -> List[Dict[str, Any]]:
        document = load_json(data_file(data_dir, "detectors.json"))
        rows = document.get("detectors")
        if not isinstance(rows, list):
            raise AgentError("detectors.json must contain a 'detectors' list")
        compiled: List[Dict[str, Any]] = []
        for row in rows:
            try:
                pattern: Pattern[str] = re.compile(str(row["pattern"]))
            except (KeyError, re.error) as exc:
                raise AgentError("bad detector entry {!r}: {}".format(row, exc)) from exc
            compiled.append({**row, "compiled": pattern})
        return compiled

    def run(self, ctx: AgentContext) -> Dict[str, Any]:
        path = ctx.asset_path()
        if not path.is_file():
            return {
                "status": "incomplete",
                "missing_inputs": ["asset"],
                "citations": [],
                "reason": "asset not readable: {}".format(path.name),
                "asset": path.name,
            }
        text = path.read_text(encoding="utf-8", errors="replace")
        hits: List[Dict[str, Any]] = []
        levels: List[str] = []
        for detector in self._compiled(ctx.data_dir):
            matches = detector["compiled"].findall(text)
            if not matches:
                continue
            flat = [m if isinstance(m, str) else m[0] for m in matches]
            prefix = int(detector.get("keep_prefix", 0))
            suffix = int(detector.get("keep_suffix", 0))
            samples = [mask(value, prefix, suffix) for value in flat[:3]]
            hits.append(
                {
                    "name": detector["name"],
                    "label": detector.get("label", detector["name"]),
                    "level": detector["level"],
                    "hits": len(flat),
                    "samples": samples,
                }
            )
            levels.append(str(detector["level"]))
        level = highest_level(levels, LEVEL_ORDER) or "P1"
        hits.sort(key=lambda item: (-LEVEL_ORDER.index(str(item["level"])), str(item["name"])))
        return {
            "status": "ok",
            "asset": path.name,
            "level": level,
            "detector_names": [hit["name"] for hit in hits],
            "detectors": hits,
            "scanned_chars": len(text),
            "citations": [{"id": "artifact:{}".format(path.name), "kind": "artifact"}],
        }


__all__ = ["ClassificationAgent"]
