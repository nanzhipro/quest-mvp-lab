#!/usr/bin/env python3
"""Compare base vs fine-tuned PromptGuard 2 on holdout + the original corpus.

Three question the MVP exists to answer:
  1. Does fine-tuning fix the domain false positives (security-discussion
     hard negatives, e.g. bn-03-style texts)?
  2. Does it keep attack recall (no catastrophic forgetting of the base
     model's jailbreak/injection detection)?
  3. What does it cost on general benign text?

Usage:
    uv run python scripts/eval_compare.py [--threshold 0.5]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_MODEL = ROOT / "models" / "Llama-Prompt-Guard-2-86M"
FT_MODEL = ROOT / "models" / "finetuned"
MAX_LEN = 512


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class Scorer:
    """Batch scorer over a classification model. Returns P(malicious)."""

    def __init__(self, model_dir: Path):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self.model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
        self.model.to(self.device).eval()

    def score(self, texts: list[str], batch: int = 32) -> tuple[list[float], float]:
        import torch

        scores: list[float] = []
        start = time.perf_counter()
        with torch.no_grad():
            for i in range(0, len(texts), batch):
                enc = self.tokenizer(
                    texts[i:i + batch],
                    truncation=True,
                    max_length=MAX_LEN,
                    padding=True,
                    return_tensors="pt",
                ).to(self.device)
                logits = self.model(**enc).logits
                probs = torch.softmax(logits, dim=-1)
                scores.extend(probs[:, 1].tolist())
        return scores, (time.perf_counter() - start) * 1000 / len(texts)


def confusion(rows: list[dict], scores: list[float], threshold: float) -> dict:
    tp = fp = tn = fn = 0
    errors = []
    for row, s in zip(rows, scores):
        pred = s >= threshold
        if pred and row["label"] == 1:
            tp += 1
        elif pred:
            fp += 1
            errors.append(("FP", row))
        elif row["label"] == 1:
            fn += 1
            errors.append(("FN", row))
        else:
            tn += 1
    total = tp + fp + tn + fn
    return {
        "accuracy": (tp + tn) / total,
        "tpr": tp / (tp + fn) if tp + fn else 0.0,
        "fpr": fp / (fp + tn) if fp + tn else 0.0,
        "errors": errors,
    }


def eval_set(name: str, rows: list[dict], scorers: dict[str, Scorer], threshold: float) -> dict:
    print(f"\n=== {name} (n={len(rows)}, threshold={threshold}) ===")
    texts = [r["text"] for r in rows]
    results = {}
    for model_name, scorer in scorers.items():
        scores, ms_per_sample = scorer.score(texts)
        m = confusion(rows, scores, threshold)
        results[model_name] = m
        print(f"[{model_name:>9}] acc={m['accuracy']:.2%} TPR={m['tpr']:.2%} "
              f"FPR={m['fpr']:.2%} avg_latency={ms_per_sample:.1f}ms")
        for kind, row in m["errors"]:
            s = scores[rows.index(row)]
            print(f"    {kind} score={s:.4f} cat={row['category']} :: {row['text'][:80]!r}")

    # per-category breakdown for the fine-tuned model
    by_cat = defaultdict(list)
    for row in rows:
        by_cat[row["category"]].append(row)
    if by_cat:
        print(f"  per-category (finetuned):")
        ft_scores, _ = scorers["finetuned"].score(texts)
        score_by_text = dict(zip(texts, ft_scores))
        for cat, cat_rows in sorted(by_cat.items()):
            cat_scores = [score_by_text[r["text"]] for r in cat_rows]
            m = confusion(cat_rows, cat_scores, threshold)
            print(f"    {cat:<32} acc={m['accuracy']:.2%} TPR={m['tpr']:.2%} FPR={m['fpr']:.2%} (n={len(cat_rows)})")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    if not FT_MODEL.exists():
        print(f"fine-tuned model not found at {FT_MODEL}; run scripts/train.py first", file=sys.stderr)
        return 1

    scorers = {"base": Scorer(BASE_MODEL), "finetuned": Scorer(FT_MODEL)}

    # 1. holdout test split (same distribution as training — measures fit)
    eval_set("holdout test", load_jsonl(ROOT / "data" / "test.jsonl"), scorers, args.threshold)

    # 2. original 19-sample corpus (out-of-training-distribution — measures transfer)
    corpus = json.loads((ROOT / "data" / "eval_corpus.json").read_text())["samples"]
    rows = [
        {
            "text": s["text"],
            "label": 1 if s["expect_malicious"] else 0,
            "category": f"orig_{s['category']}",
            "id": s["id"],
        }
        for s in corpus
    ]
    eval_set("original corpus (ADR scenario)", rows, scorers, args.threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
