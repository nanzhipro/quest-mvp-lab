"""审计链：留痕完整性与"改过一行就能被查出来"。"""

from __future__ import annotations

import json
from pathlib import Path

from agent_guardrails.audit import GENESIS, AuditLog, iter_audit, verify_chain


def test_entries_are_chained(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    first = audit.record("gate.decision", tool="read_file", decision="allow")
    second = audit.record("tool.exec", tool="read_file", ok=True)
    assert first.prev == GENESIS
    assert second.prev == first.digest
    assert audit.head == second.digest
    assert [entry.seq for entry in audit.entries] == [1, 2]


def test_file_holds_one_json_record_per_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("run.start", session="abc", goal="读文件")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "run.start"
    assert payload["fields"]["session"] == "abc"
    assert set(payload) == {"seq", "ts", "event", "fields", "prev", "digest"}


def test_a_fresh_run_starts_a_fresh_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("run.start")
    AuditLog(path).record("run.start")
    assert [entry["seq"] for entry in iter_audit(path)] == [1]


def test_verification_passes_on_an_untouched_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    for index in range(5):
        audit.record("gate.decision", tool="read_file", index=index)
    verification = verify_chain(path)
    assert verification.ok
    assert verification.entries == 5


def test_editing_a_record_is_detected(tmp_path: Path) -> None:
    """这正是哈希链存在的唯一理由：事后改写能被离线复算发现。"""
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    audit.record("gate.decision", tool="send_email", decision="deny")
    audit.record("tool.exec", tool="send_email")
    raw = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    raw[0]["fields"]["decision"] = "allow"  # 篡改：把拒绝说成放行
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in raw) + "\n")

    verification = verify_chain(path)
    assert not verification.ok
    assert "摘要不匹配" in verification.reason


def test_deleting_a_record_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    audit.record("run.start")
    audit.record("gate.decision", decision="deny")
    audit.record("run.end")
    kept = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([kept[0], kept[2]]) + "\n")

    verification = verify_chain(path)
    assert not verification.ok
    assert "prev" in verification.reason


def test_memory_only_audit_needs_no_file(tmp_path: Path) -> None:
    audit = AuditLog(None)
    audit.record("gate.decision", decision="deny", rule="gate.arg_rule")
    assert audit.entries[0].fields["rule"] == "gate.arg_rule"
    assert not (tmp_path / "audit.jsonl").exists()
