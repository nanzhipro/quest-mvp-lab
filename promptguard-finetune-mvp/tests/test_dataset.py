"""Dataset integrity tests — no model required."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPLITS = ["train", "val", "test"]

VALID_LABELS = {0, 1}
VALID_CATEGORIES = {
    "jailbreak_direct",
    "indirect_injection",
    "indirect_injection_subtle",
    "benign_general",
    "benign_security_discussion",
    "benign_instruction_lookalike",
}


def load(name: str) -> list[dict]:
    path = ROOT / "data" / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_splits_exist_and_nonempty() -> None:
    for name in SPLITS:
        rows = load(name)
        assert rows, f"data/{name}.jsonl missing or empty; run scripts/build_dataset.py"


def test_schema_and_label_parity() -> None:
    for name in SPLITS:
        for row in load(name):
            assert row["label"] in VALID_LABELS
            assert row["category"] in VALID_CATEGORIES
            assert row["text"].strip()
            # label must agree with category semantics
            if row["category"].startswith("benign"):
                assert row["label"] == 0, f"benign category with label=1: {row['text'][:60]}"
            else:
                assert row["label"] == 1, f"attack category with label=0: {row['text'][:60]}"


def test_no_leakage_between_splits() -> None:
    """The same normalized text must never appear in two splits."""
    import hashlib
    import re

    def norm(t: str) -> str:
        return hashlib.md5(re.sub(r"[^\w一-鿿]+", "", t.lower()).encode()).hexdigest()

    seen: dict[str, str] = {}
    for name in SPLITS:
        for row in load(name):
            h = norm(row["text"])
            assert h not in seen, f"leakage: text in both {seen[h]} and {name}: {row['text'][:60]}"
            seen[h] = name


def test_class_balance_is_sane() -> None:
    """Both classes present in every split; no category dominates the train set."""
    train = load("train")
    counts = Counter(r["category"] for r in train)
    total = len(train)
    for cat, n in counts.items():
        assert n / total < 0.35, f"{cat} dominates train set ({n}/{total})"
    for name in SPLITS:
        labels = Counter(r["label"] for r in load(name))
        assert labels[0] > 0 and labels[1] > 0, f"{name} split missing a class"
