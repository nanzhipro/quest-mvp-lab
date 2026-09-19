"""Tolerant-JSON and finding tests — the layer that decides "re-ask" vs "fall back"."""

from __future__ import annotations

from supervisor_graph_mvp.jsonio import (
    Finding,
    ParseResult,
    first_json_object,
    parse_object,
    require_keys,
    string_list,
    strip_code_fence,
)


def test_strip_code_fence_removes_a_json_wrapper():
    assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fence('{"a": 1}') == '{"a": 1}'


def test_first_json_object_is_string_and_escape_aware():
    text = 'prefix {"a": "}{ not a boundary", "b": {"c": 1}} suffix'
    assert first_json_object(text) == '{"a": "}{ not a boundary", "b": {"c": 1}}'
    escaped = r'{"path": "C:\\dir\\", "n": 1}'
    assert first_json_object(escaped) == escaped
    assert first_json_object("no braces here") is None


def test_parse_object_handles_prose_and_fences():
    parsed = parse_object('好的，计划如下：\n```json\n{"subtasks": []}\n```\n以上。')
    assert parsed.ok and parsed.value == {"subtasks": []}


def test_parse_object_reports_missing_and_malformed_json():
    missing = parse_object("完全没有 JSON")
    assert not missing.ok and "no JSON object" in missing.error
    malformed = parse_object("{not json}")
    assert not malformed.ok and "invalid JSON" in malformed.error
    array = parse_object("[1, 2]")
    assert not array.ok


def test_parse_result_defaults():
    assert ParseResult().ok is False
    assert ParseResult(value={}).ok is True


def test_require_keys_lists_missing_and_null_fields():
    assert require_keys({"a": 1, "b": None}, ["a", "b", "c"]) == ["b", "c"]


def test_string_list_normalises_and_deduplicates():
    result = string_list({"intents": [" a ", "b", "a", ""]}, "intents")
    assert result.ok and result.values == ("a", "b")


def test_string_list_accepts_a_single_string_and_rejects_junk():
    assert string_list({"x": "solo"}, "x").values == ("solo",)
    assert not string_list({"x": [1]}, "x").ok
    assert not string_list({}, "x").ok
    assert not string_list({"x": []}, "x").ok
    assert string_list({"x": []}, "x", allow_empty=True).ok


def test_finding_round_trips_and_classifies_severity():
    finding = Finding(
        id="C4#x",
        rule="C4-evidence",
        severity="blocking",
        message="证据缺失",
        repair={"agent": "evidence", "goal": "重新核验"},
        detail={"evidence": {"id": "artifact:x"}},
    )
    assert finding.blocking is True
    assert Finding.from_dict(finding.as_dict()) == finding
    assert Finding.from_dict({}).blocking is False
