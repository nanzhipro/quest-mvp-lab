#!/usr/bin/env python3
"""Validate, dedup, and split seed batches into train/val/test.

Seeds live in ``data/seeds/*.jsonl``, one JSON object per line::

    {"text": "...", "label": 0|1, "category": "...", "lang": "en|zh|mixed", "carrier": "..."}

- label: 1 = malicious (jailbreak / injection), 0 = benign
- category: e.g. jailbreak_direct, indirect_injection, benign_general,
  benign_security_discussion (hard negative), benign_instruction_lookalike
- carrier: surface the text mimics — user_message, tool_json, tool_text,
  email, html_comment, markdown, log, code_comment, ...

Output: data/{train,val,test}.jsonl (stratified by category) + stats.

Usage:
    uv run python scripts/build_dataset.py [--val-ratio 0.12] [--test-ratio 0.12]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDS_DIR = ROOT / "data" / "seeds"

REQUIRED_KEYS = {"text", "label", "category", "lang", "carrier"}
VALID_LABELS = {0, 1}


def normalize(text: str) -> str:
    """Aggressive normalization for near-duplicate detection."""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w一-鿿]+", "", text)
    return text


def load_seeds() -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    for path in sorted(SEEDS_DIR.glob("*.jsonl")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{path.name}:{lineno}: bad JSON: {e}")
                continue
            missing = REQUIRED_KEYS - row.keys()
            if missing:
                errors.append(f"{path.name}:{lineno}: missing keys {missing}")
                continue
            if row["label"] not in VALID_LABELS:
                errors.append(f"{path.name}:{lineno}: label must be 0/1")
                continue
            if not row["text"].strip():
                errors.append(f"{path.name}:{lineno}: empty text")
                continue
            rows.append(row)
    return rows, errors


def dedup(rows: list[dict]) -> tuple[list[dict], int]:
    """Drop exact and normalized-text duplicates. Returns (kept, dropped)."""
    seen_exact, seen_norm = set(), set()
    kept = []
    dropped = 0
    for row in rows:
        key_exact = row["text"].strip()
        key_norm = hashlib.md5(normalize(row["text"]).encode()).hexdigest()
        if key_exact in seen_exact or key_norm in seen_norm:
            dropped += 1
            continue
        seen_exact.add(key_exact)
        seen_norm.add(key_norm)
        kept.append(row)
    return kept, dropped


def split_stratified(rows: list[dict], val_ratio: float, test_ratio: float,
                     seed: int = 42) -> tuple[list[dict], list[dict], list[dict]]:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_cat[row["category"]].append(row)
    rng = random.Random(seed)
    train, val, test = [], [], []
    for cat_rows in by_cat.values():
        rng.shuffle(cat_rows)
        n = len(cat_rows)
        n_test = max(1, round(n * test_ratio)) if n >= 5 else 0
        n_val = max(1, round(n * val_ratio)) if n >= 5 else 0
        test.extend(cat_rows[:n_test])
        val.extend(cat_rows[n_test:n_test + n_val])
        train.extend(cat_rows[n_test + n_val:])
    rng.shuffle(train)
    return train, val, test


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_stats(name: str, rows: list[dict]) -> None:
    labels = Counter(r["label"] for r in rows)
    cats = Counter(r["category"] for r in rows)
    langs = Counter(r["lang"] for r in rows)
    print(f"\n[{name}] n={len(rows)}  malicious={labels[1]} benign={labels[0]}")
    print(f"  lang: {dict(langs)}")
    for cat, n in sorted(cats.items()):
        print(f"  {cat:<32} {n}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--val-ratio", type=float, default=0.12)
    parser.add_argument("--test-ratio", type=float, default=0.12)
    args = parser.parse_args()

    rows, errors = load_seeds()
    if errors:
        print("SCHEMA ERRORS:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    if not rows:
        print(f"no seed rows found in {SEEDS_DIR}", file=sys.stderr)
        return 1

    print(f"loaded {len(rows)} seed rows from {SEEDS_DIR}")
    rows, dropped = dedup(rows)
    print(f"dedup: dropped {dropped}, kept {len(rows)}")

    train, val, test = split_stratified(rows, args.val_ratio, args.test_ratio)
    write_jsonl(ROOT / "data" / "train.jsonl", train)
    write_jsonl(ROOT / "data" / "val.jsonl", val)
    write_jsonl(ROOT / "data" / "test.jsonl", test)
    print_stats("train", train)
    print_stats("val", val)
    print_stats("test", test)
    print("\nwrote data/{train,val,test}.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
