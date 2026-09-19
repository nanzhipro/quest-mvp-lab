"""Intentionally incorrect learner starting points. Never use in real systems.

Fix each function against the README contract without editing the contract suite.
"""

from copy import deepcopy
from .storage import save_state


def decide(actions):
    chosen = next((a for a in ("deny", "warn", "audit") if a in actions), "allow")
    return {"action": chosen, "blocked": chosen == "deny",
            "notice": chosen != "allow", "reported": chosen != "allow"}


def migrate(state, capacity):
    updated = deepcopy(state)
    updated["capacity"] = capacity
    return updated


def deliver(peer, event_id, payload, max_attempts=3):
    for attempt in range(1, max_attempts + 1):
        try:
            return peer(event_id=f"{event_id}-{attempt}", attempt=attempt, payload=payload)
        except TimeoutError:
            if attempt == max_attempts:
                raise


def verify_chain(records):
    return any(r.get("kind") == "ack" for r in records)
