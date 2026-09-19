"""The tiny deterministic predicate language the agents' *data files* are written in.

Rules (``policy_rules.json``) and actions (``remediation_actions.json``) both say
when they apply as a list of ``{"op": ..., "value": ...}`` clauses that must all
hold — a conjunction, no nesting, no side effects. Keeping it this small is the
point: a reviewer can read a rule and predict its behaviour without running code,
and the same data files stay usable if the engine is ever replaced.

Unknown operators raise instead of silently evaluating false: a typo in a policy
file must be a loud failure, never a quietly permissive rule.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .agents.base import AgentError

LEVEL_ORDER = ("P1", "P2", "P3", "P4")


def level_rank(level: Any, order: Sequence[str] = LEVEL_ORDER) -> Optional[int]:
    """Position of a level in the order, or ``None`` when the level is unknown."""
    if not isinstance(level, str):
        return None
    try:
        return list(order).index(level.strip().upper())
    except ValueError:
        return None


def _cmp_level(variables: Mapping[str, Any], value: Any, order: Sequence[str]) -> Optional[int]:
    """Signed comparison of the row's level against a clause value, or ``None``."""
    left = level_rank(variables.get("level"), order)
    right = level_rank(value, order)
    if left is None or right is None:
        return None
    return left - right


def evaluate_ops(
    ops: Iterable[Mapping[str, Any]],
    variables: Mapping[str, Any],
    *,
    order: Sequence[str] = LEVEL_ORDER,
) -> bool:
    """True when every clause holds. An empty clause list means "always applies"."""
    for clause in ops or ():
        op = str(clause.get("op") or "")
        value = clause.get("value")
        if op == "always":
            continue
        if op == "level_unknown":
            if level_rank(variables.get("level"), order) is not None:
                return False
            continue
        if op in ("level_gte", "level_lte", "level_eq"):
            delta = _cmp_level(variables, value, order)
            if delta is None:
                return False
            if op == "level_gte" and delta < 0:
                return False
            if op == "level_lte" and delta > 0:
                return False
            if op == "level_eq" and delta != 0:
                return False
            continue
        if op == "channel_in":
            if str(variables.get("channel") or "") not in list(value or []):
                return False
            continue
        if op == "channel_not_in":
            if str(variables.get("channel") or "") in list(value or []):
                return False
            continue
        if op == "destination_type_eq":
            if str(variables.get("destination_type") or "") != str(value or ""):
                return False
            continue
        if op == "decision_eq":
            if str(variables.get("decision") or "") != str(value or ""):
                return False
            continue
        if op == "detector_hit":
            hits = variables.get("detectors") or {}
            if not isinstance(hits, Mapping) or int(hits.get(str(value), 0)) <= 0:
                return False
            continue
        raise AgentError("unknown predicate op {!r} in data file".format(op))
    return True


def strictest_decision(decisions: Iterable[str], precedence: Sequence[str]) -> str:
    """Pick the most restrictive decision present, following the ruleset's precedence."""
    order = list(precedence)
    best = ""
    for decision in decisions:
        if decision not in order:
            continue
        if not best or order.index(decision) < order.index(best):
            best = decision
    return best


def tightest_threshold(levels: Iterable[str], order: Sequence[str]) -> str:
    """The most restrictive (numerically lowest) threshold among the matched rules."""
    ranks: List[int] = [
        rank for rank in (level_rank(level, order) for level in levels) if rank is not None
    ]
    if not ranks:
        return ""
    return list(order)[min(ranks)]


def highest_level(levels: Iterable[Any], order: Sequence[str]) -> str:
    """The highest level present (used by the classifier to roll hits into a level)."""
    ranks = [rank for rank in (level_rank(level, order) for level in levels) if rank is not None]
    if not ranks:
        return ""
    return list(order)[max(ranks)]


def as_variables(**kwargs: Any) -> Dict[str, Any]:
    """Small helper that keeps call sites readable and explicit."""
    return dict(kwargs)
