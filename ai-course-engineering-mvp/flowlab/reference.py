"""Reference behavior for four course exercises, using only synthetic data."""

from copy import deepcopy
from .storage import events, save_state


def decide(actions):
    """Audit records silently; warning adds a notice; deny also blocks."""
    allowed = {"audit", "warn", "deny"}
    if any(action not in allowed for action in actions):
        raise ValueError("unknown action")
    chosen = next((a for a in ("deny", "warn", "audit") if a in actions), "allow")
    return {"action": chosen, "blocked": chosen == "deny",
            "notice": chosen in {"warn", "deny"}, "reported": chosen != "allow"}


def migrate(state, capacity):
    """Canonicalize to head=0; shrinking retains the newest events."""
    if type(capacity) is not int or capacity <= 0:
        raise ValueError("capacity must be a positive integer")
    retained = events(state)[-capacity:]
    return {"capacity": capacity, "head": 0, "count": len(retained),
            "slots": {str(i): item for i, item in enumerate(retained)}}


def deliver(peer, event_id, payload, max_attempts=3):
    """Retry transport failures with stable business identity; attempts are separate."""
    if not isinstance(event_id, str) or not event_id or type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("event identity and a positive attempt limit required")
    for attempt in range(1, max_attempts + 1):
        try:
            return peer(event_id=event_id, attempt=attempt, payload=deepcopy(payload))
        except TimeoutError:
            if attempt == max_attempts:
                raise


def verify_chain(records):
    """Reject incomplete, duplicated, or mismatched client/peer evidence.

    Scope: one successful delivery, possibly with timed-out attempts; sequential
    records with event_id/kind/attempt. This is a lab contract, not a production
    telemetry format or a proof of transport authenticity.
    """
    if not records:
        return False
    identities = {r.get("event_id") for r in records}
    if len(identities) != 1 or not all(isinstance(x, str) and x for x in identities):
        return False
    attempts = {}
    for record in records:
        kind, attempt = record.get("kind"), record.get("attempt")
        if kind not in {"send", "accepted", "timeout", "ack"} or type(attempt) is not int or attempt < 1:
            return False
        kinds = attempts.setdefault(attempt, [])
        if kind in kinds:
            return False
        kinds.append(kind)
    numbers = list(attempts)
    if numbers != list(range(1, len(numbers) + 1)):
        return False
    expected_order = sorted(records, key=lambda r: r["attempt"])
    if records != expected_order:
        return False
    accepted = sum(r["kind"] == "accepted" for r in records)
    acknowledgements = sum(r["kind"] == "ack" for r in records)
    if accepted != 1 or acknowledgements != 1:
        return False
    for attempt, kinds in attempts.items():
        if attempt != numbers[-1]:
            if kinds not in (["send", "timeout"], ["send", "accepted", "timeout"]):
                return False
        elif kinds not in (["send", "ack"], ["send", "accepted", "ack"]):
            return False
    accepted_at = next(i for i, r in enumerate(records) if r["kind"] == "accepted")
    ack_at = next(i for i, r in enumerate(records) if r["kind"] == "ack")
    return accepted_at < ack_at
