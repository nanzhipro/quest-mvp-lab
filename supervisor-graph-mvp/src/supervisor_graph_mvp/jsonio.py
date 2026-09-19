"""Tolerant JSON extraction plus the tiny schema checks the Supervisor relies on.

A routing agent lives or dies by how it handles a model reply that is *almost* the
JSON it asked for. This module is the whole answer to that: pull the first balanced
object out of whatever prose the model wrapped it in, then check the handful of
fields the next stage actually needs. Nothing here raises on bad input — every
function returns a value plus a readable reason, so the caller (the Supervisor)
can decide between "re-ask once" and "fall back to the deterministic template".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ParseResult:
    """Outcome of trying to read a JSON object out of a model reply."""

    value: Optional[Dict[str, Any]] = None
    error: str = ""
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None


def strip_code_fence(text: str) -> str:
    """Remove a ```json … ``` wrapper (the single most common model decoration)."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if "\n" in body:
        body = body.split("\n", 1)[1]
    fence = body.rfind("```")
    if fence != -1:
        body = body[:fence]
    return body.strip()


def first_json_object(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` region of ``text``, string-aware.

    Braces inside JSON strings (and escaped quotes) must not unbalance the scan —
    that is the whole reason this is a scanner and not ``text.find('}')``.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return None


def parse_object(text: str) -> ParseResult:
    """Read a JSON object out of a model reply, tolerating fences and surrounding prose."""
    candidate = first_json_object(strip_code_fence(text or ""))
    if candidate is None:
        return ParseResult(error="no JSON object found in reply", raw=text or "")
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return ParseResult(error="invalid JSON: {}".format(exc), raw=candidate)
    if not isinstance(value, dict):
        return ParseResult(error="top-level JSON value is not an object", raw=candidate)
    return ParseResult(value=value, raw=candidate)


def require_keys(payload: Dict[str, Any], keys: List[str]) -> List[str]:
    """Return the list of missing (or explicitly null) required keys."""
    missing: List[str] = []
    for key in keys:
        if key not in payload or payload[key] is None:
            missing.append(key)
    return missing


@dataclass(frozen=True)
class StringList:
    """A required list-of-strings field, normalised (trimmed, de-duplicated, ordered)."""

    values: Tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def string_list(payload: Dict[str, Any], key: str, *, allow_empty: bool = False) -> StringList:
    """Coerce ``payload[key]`` into a de-duplicated tuple[str, ...] or explain why not."""
    if key not in payload:
        return StringList(error="missing field '{}'".format(key))
    raw = payload[key]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return StringList(error="field '{}' must be a list of strings".format(key))
    seen: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            return StringList(error="field '{}' contains a non-string item".format(key))
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    if not seen and not allow_empty:
        return StringList(error="field '{}' must not be empty".format(key))
    return StringList(values=tuple(seen))


@dataclass(frozen=True)
class Finding:
    """One consistency-check outcome.

    ``severity`` decides what the graph does next: ``blocking`` findings are routed
    back to the dispatch node for the listed repair, ``warning`` findings only show
    up in the report. ``repair`` names the specialist that must redo work — the
    Supervisor never repairs anything itself.
    """

    id: str
    rule: str
    severity: str
    message: str
    repair: Optional[Dict[str, str]] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "repair": dict(self.repair) if self.repair else None,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Finding":
        return cls(
            id=str(payload.get("id", "")),
            rule=str(payload.get("rule", "")),
            severity=str(payload.get("severity", "warning")),
            message=str(payload.get("message", "")),
            repair=payload.get("repair"),
            detail=payload.get("detail") or {},
        )
