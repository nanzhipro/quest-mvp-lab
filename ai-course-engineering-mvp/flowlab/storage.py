"""Given file and ring primitives for the synthetic classroom project."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile


def validate_state(state):
    """Validate the v1 ring; slots outside the live range may hold stale data."""
    capacity, head, count = (state.get(k) for k in ("capacity", "head", "count"))
    if any(type(n) is not int for n in (capacity, head, count)):
        raise ValueError("integer metadata required")
    if capacity <= 0 or not 0 <= head < capacity or not 0 <= count <= capacity:
        raise ValueError("invalid ring metadata")
    slots = state.get("slots")
    if not isinstance(slots, dict):
        raise ValueError("slots must be a mapping")
    for offset in range(count):
        if str((head + offset) % capacity) not in slots:
            raise ValueError("missing live slot")


def events(state):
    validate_state(state)
    return [deepcopy(state["slots"][str((state["head"] + i) % state["capacity"])])
            for i in range(state["count"])]


def save_state(path, state, before_replace=None):
    """Replace one file after validation; this is not a multi-file transaction.

    before_replace is a deterministic crash-injection point used by the lab.
    """
    validate_state(state)
    path = Path(path)
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".flowlab-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if before_replace:
            before_replace()
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


