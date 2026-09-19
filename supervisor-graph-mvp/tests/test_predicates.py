"""Predicate-language tests: the ops that policy and action data files are written in."""

from __future__ import annotations

import pytest

from supervisor_graph_mvp.agents.base import AgentError
from supervisor_graph_mvp.predicates import (
    LEVEL_ORDER,
    evaluate_ops,
    highest_level,
    level_rank,
    strictest_decision,
    tightest_threshold,
)


def test_level_rank_and_order():
    assert level_rank("P1") == 0
    assert level_rank("p4") == 3
    assert level_rank("PX") is None
    assert level_rank(None) is None
    assert LEVEL_ORDER == ("P1", "P2", "P3", "P4")


def test_empty_clause_list_always_applies():
    assert evaluate_ops([], {}) is True


def test_always_and_level_unknown_ops():
    assert evaluate_ops([{"op": "always"}], {}) is True
    assert evaluate_ops([{"op": "level_unknown"}], {"level": ""}) is True
    assert evaluate_ops([{"op": "level_unknown"}], {"level": "P2"}) is False


def test_level_comparisons():
    variables = {"level": "P3"}
    assert evaluate_ops([{"op": "level_gte", "value": "P3"}], variables) is True
    assert evaluate_ops([{"op": "level_gte", "value": "P4"}], variables) is False
    assert evaluate_ops([{"op": "level_lte", "value": "P3"}], variables) is True
    assert evaluate_ops([{"op": "level_eq", "value": "P3"}], variables) is True
    assert evaluate_ops([{"op": "level_eq", "value": "P2"}], variables) is False


def test_level_comparison_is_false_when_the_level_is_unknown():
    assert evaluate_ops([{"op": "level_gte", "value": "P2"}], {}) is False


def test_channel_and_destination_ops():
    variables = {"channel": "webmail", "destination_type": "external"}
    assert evaluate_ops([{"op": "channel_in", "value": ["webmail", "im"]}], variables) is True
    assert evaluate_ops([{"op": "channel_not_in", "value": ["webmail"]}], variables) is False
    assert evaluate_ops([{"op": "destination_type_eq", "value": "external"}], variables) is True
    assert evaluate_ops([{"op": "destination_type_eq", "value": "internal"}], variables) is False


def test_decision_equality_op():
    assert evaluate_ops([{"op": "decision_eq", "value": "block"}], {"decision": "block"}) is True
    assert evaluate_ops([{"op": "decision_eq", "value": "review"}], {"decision": "block"}) is False


def test_detector_hit_op_requires_a_positive_count():
    variables = {"detectors": {"id_card": 2, "api_key": 0}}
    assert evaluate_ops([{"op": "detector_hit", "value": "id_card"}], variables) is True
    assert evaluate_ops([{"op": "detector_hit", "value": "api_key"}], variables) is False
    assert evaluate_ops([{"op": "detector_hit", "value": "absent"}], variables) is False
    assert evaluate_ops([{"op": "detector_hit", "value": "id_card"}], {}) is False


def test_clauses_are_conjunctive():
    ops = [{"op": "level_gte", "value": "P2"}, {"op": "destination_type_eq", "value": "internal"}]
    assert evaluate_ops(ops, {"level": "P2", "destination_type": "external"}) is False
    assert evaluate_ops(ops, {"level": "P2", "destination_type": "internal"}) is True


def test_unknown_op_raises_instead_of_failing_open():
    with pytest.raises(AgentError, match="unknown predicate op"):
        evaluate_ops([{"op": "looks_sensitive"}], {"level": "P4"})


def test_strictest_decision_follows_precedence():
    precedence = ["block", "review", "allow"]
    assert strictest_decision(["allow", "review"], precedence) == "review"
    assert strictest_decision(["review", "block", "allow"], precedence) == "block"
    assert strictest_decision([], precedence) == ""
    assert strictest_decision(["nonsense"], precedence) == ""


def test_tightest_threshold_and_highest_level():
    assert tightest_threshold(["P4", "P2", "P3"], LEVEL_ORDER) == "P2"
    assert tightest_threshold([], LEVEL_ORDER) == ""
    assert highest_level(["P1", "P3", "P2"], LEVEL_ORDER) == "P3"
    assert highest_level([], LEVEL_ORDER) == ""
