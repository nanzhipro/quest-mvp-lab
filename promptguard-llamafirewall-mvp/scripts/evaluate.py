#!/usr/bin/env python3
"""Evaluate PromptGuard 2 against the labeled corpus and print a report.

Usage:
    uv run python scripts/evaluate.py [--engine guard|firewall] [--bench N]

Outputs per-sample verdicts, confusion metrics per category, and latency stats.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from promptguard_mvp.guard import PromptGuard  # noqa: E402


def evaluate(engine: str) -> int:
    corpus = json.loads((ROOT / "data" / "corpus.json").read_text())["samples"]

    if engine == "firewall":
        from promptguard_mvp.firewall import FirewallGate

        gate = FirewallGate()
        scan = lambda s: gate.scan(s["text"], role=s["surface"])  # noqa: E731
        is_mal = lambda v: v.blocked  # noqa: E731
        score_of = lambda v: v.score  # noqa: E731
        latency_of = lambda v: None
    else:
        guard = PromptGuard()
        scan = lambda s: guard.scan(s["text"])  # noqa: E731
        is_mal = lambda v: v.is_malicious  # noqa: E731
        score_of = lambda v: v.malicious_score  # noqa: E731
        latency_of = lambda v: v.latency_ms  # noqa: E731

    rows = []
    latencies = []
    for sample in corpus:
        verdict = scan(sample)
        predicted = is_mal(verdict)
        expected = sample["expect_malicious"]
        ok = predicted == expected
        lat = latency_of(verdict)
        if lat is not None:
            latencies.append(lat)
        rows.append(
            {
                "id": sample["id"],
                "category": sample["category"],
                "expected": "MALICIOUS" if expected else "BENIGN",
                "predicted": "MALICIOUS" if predicted else "BENIGN",
                "score": round(score_of(verdict), 4),
                "ok": ok,
                "latency_ms": round(lat, 1) if lat is not None else None,
            }
        )

    tp = sum(1 for r in rows if r["expected"] == "MALICIOUS" and r["predicted"] == "MALICIOUS")
    fn = sum(1 for r in rows if r["expected"] == "MALICIOUS" and r["predicted"] == "BENIGN")
    tn = sum(1 for r in rows if r["expected"] == "BENIGN" and r["predicted"] == "BENIGN")
    fp = sum(1 for r in rows if r["expected"] == "BENIGN" and r["predicted"] == "MALICIOUS")
    total = len(rows)

    print(f"\n=== PromptGuard 2 (86M) evaluation — engine={engine} ===\n")
    for r in rows:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"[{mark}] {r['id']:>6} {r['category']:<19} exp={r['expected']:<9} "
              f"pred={r['predicted']:<9} score={r['score']:.4f}"
              + (f" latency={r['latency_ms']}ms" if r["latency_ms"] is not None else ""))

    print("\n=== Metrics ===")
    print(f"accuracy  : {tp + tn}/{total} = {(tp + tn) / total:.2%}")
    if tp + fn:
        print(f"TPR (recall on attacks) : {tp}/{tp + fn} = {tp / (tp + fn):.2%}")
    if tn + fp:
        print(f"FPR (false alarm on benign): {fp}/{tn + fp} = {fp / (tn + fp):.2%}")
    if latencies:
        print(f"latency ms: median={statistics.median(latencies):.1f} "
              f"mean={statistics.mean(latencies):.1f} max={max(latencies):.1f}")

    failed = [r["id"] for r in rows if not r["ok"]]
    if failed:
        print(f"\nMisclassified: {', '.join(failed)}")
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["guard", "firewall"], default="guard")
    args = parser.parse_args()
    return evaluate(args.engine)


if __name__ == "__main__":
    sys.exit(main())
