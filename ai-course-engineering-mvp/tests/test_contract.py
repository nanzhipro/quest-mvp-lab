"""Behavioral contracts: same suite grades the reference and learner module.

Usage: FLOWLAB_CANDIDATE=starter python3 -m unittest discover -s tests -v
"""

from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest

candidate = os.environ.get("FLOWLAB_CANDIDATE", "reference")
if candidate not in {"reference", "starter"}:
    raise ValueError("FLOWLAB_CANDIDATE must be reference or starter")
subject = importlib.import_module(f"flowlab.{candidate}")


def ring(items, capacity, head):
    return {"capacity": capacity, "head": head, "count": len(items),
            "slots": {str((head + i) % capacity): deepcopy(item) for i, item in enumerate(items)}}


def read_ring(state):
    return [state["slots"][str((state["head"] + i) % state["capacity"])]
            for i in range(state["count"])]


def record(kind, attempt=1, event_id="evt-a"):
    return {"event_id": event_id, "kind": kind, "attempt": attempt}


class DecisionContract(unittest.TestCase):
    def test_existing_audit_is_silent(self):
        self.assertEqual(subject.decide(["audit"]),
                         {"action": "audit", "blocked": False, "notice": False, "reported": True})

    def test_new_warning_preserves_data(self):
        self.assertEqual(subject.decide(["warn", "audit"]),
                         {"action": "warn", "blocked": False, "notice": True, "reported": True})

    def test_deny_has_priority_in_any_order(self):
        import itertools
        for actions in itertools.permutations(["deny", "warn", "audit"]):
            with self.subTest(actions=actions):
                self.assertEqual(subject.decide(actions),
                                 {"action": "deny", "blocked": True, "notice": True, "reported": True})

    def test_no_policy_and_unknown_policy_are_different(self):
        self.assertEqual(subject.decide([]),
                         {"action": "allow", "blocked": False, "notice": False, "reported": False})
        with self.assertRaises(ValueError):
            subject.decide(["warning_typo"])


class MigrationContract(unittest.TestCase):
    def test_wrapped_existing_data_survives_expansion(self):
        old = ring(["A", "B", "C", "D"], 5, 3)
        result = subject.migrate(old, 8)
        self.assertEqual(read_ring(result), ["A", "B", "C", "D"])
        self.assertEqual(result["capacity"], 8)

    def test_shrink_keeps_newest_and_is_idempotent(self):
        result = subject.migrate(ring(["A", "B", "C", "D"], 5, 3), 2)
        self.assertEqual(read_ring(result), ["C", "D"])
        self.assertEqual(subject.migrate(result, 2), result)

    def test_migration_does_not_mutate_the_input(self):
        old = ring([{"value": "A"}, {"value": "B"}], 3, 2)
        before = deepcopy(old)
        result = subject.migrate(old, 5)
        result["slots"][str(result["head"])]["value"] = "changed"
        self.assertEqual(old, before)

    def test_capacity_head_count_matrix(self):
        for capacity in range(1, 7):
            for head in range(capacity):
                for count in range(capacity + 1):
                    items = list(range(count))
                    for new in (1, 2, 4, 9):
                        with self.subTest(capacity=capacity, head=head, count=count, new=new):
                            result = subject.migrate(ring(items, capacity, head), new)
                            self.assertEqual(read_ring(result), items[-new:])
                            self.assertEqual(result["count"], min(count, new))
                            self.assertEqual(result["capacity"], new)

    def test_invalid_metadata_and_capacity_are_rejected(self):
        valid = ring(["a"], 2, 1)
        for capacity in (0, -1, True, 1.5):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                subject.migrate(valid, capacity)
        for change in ({"count": 3}, {"head": 2}, {"slots": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                subject.migrate({**valid, **change}, 4)

    def test_file_survives_failure_before_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            old = ring(["a", "b"], 3, 2)
            subject.save_state(path, old)
            previous = path.read_bytes()
            new = subject.migrate(old, 6)
            def interrupt():
                raise OSError("synthetic disk failure before replace")
            with self.assertRaises(OSError):
                subject.save_state(path, new, before_replace=interrupt)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            subject.save_state(path, new)
            self.assertEqual(read_ring(json.loads(path.read_text())), ["a", "b"])


class DeliveryContract(unittest.TestCase):
    def test_ack_loss_retries_without_duplicate_business_event(self):
        accepted, calls = {}, []
        def peer(**request):
            calls.append(request)
            accepted.setdefault(request["event_id"], request["payload"])
            if request["attempt"] == 1:
                raise TimeoutError("accepted but acknowledgement lost")
            return "ack"
        self.assertEqual(subject.deliver(peer, "evt-a", {"value": 42}), "ack")
        self.assertEqual(set(accepted), {"evt-a"})
        self.assertEqual([r["attempt"] for r in calls], [1, 2])

    def test_timeout_has_finite_budget(self):
        attempts = []
        def peer(**request):
            attempts.append(request["attempt"])
            raise TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):
            subject.deliver(peer, "evt-a", {}, max_attempts=2)
        self.assertEqual(attempts, [1, 2])

    def test_business_rejection_is_not_retried(self):
        attempts = []
        def peer(**request):
            attempts.append(request["attempt"])
            raise ValueError("invalid payload")
        with self.assertRaises(ValueError):
            subject.deliver(peer, "evt-a", {})
        self.assertEqual(attempts, [1])


class EvidenceContract(unittest.TestCase):
    def test_both_sides_accept_success_and_lost_ack_retry(self):
        self.assertTrue(subject.verify_chain([record("send"), record("accepted"), record("ack")]))
        self.assertTrue(subject.verify_chain([record("send"), record("accepted"), record("timeout"),
                                              record("send", 2), record("ack", 2)]))

    def test_missing_mixed_duplicate_or_unordered_evidence_fails(self):
        invalid = [
            [], [record("ack")], [record("send"), record("ack")],
            [record("send"), record("accepted", event_id="evt-b"), record("ack")],
            [record("send"), record("accepted"), record("ack"), record("ack")],
            [record("accepted"), record("send"), record("ack")],
            [record("send", 2), record("accepted", 2), record("ack", 2)],
            [record("send"), record("accepted"), record("timeout"), record("send", 2),
             record("accepted", 2), record("ack", 2)],
        ]
        for records in invalid:
            with self.subTest(records=records):
                self.assertFalse(subject.verify_chain(records))


if __name__ == "__main__":
    unittest.main()
