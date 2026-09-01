#!/usr/bin/env python3
"""Fine-tune PromptGuard 2 (86M, mDeBERTa-v3-base) on the synthetic dataset.

Full fine-tune (86M params is small enough that LoRA buys nothing and full FT
is the honest baseline). Key choices, each deliberate:

- lr 2e-5, 3 epochs, batch 16, cosine schedule, 10% warmup — the standard
  BERT-family fine-tune envelope; higher lr on a small curated set is the
  classic route to catastrophic forgetting of the base model's attack recall.
- Early stopping on eval F1 (malicious class) with best-checkpoint reload.
- fp32 on Apple-Silicon MPS (bf16 support is spotty; 86M doesn't need it).

Usage:
    uv run python scripts/train.py [--epochs 3] [--lr 2e-5] [--batch 16]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BASE_MODEL = ROOT / "models" / "Llama-Prompt-Guard-2-86M"
OUT_DIR = ROOT / "models" / "finetuned"
MAX_LEN = 512  # PromptGuard 2's native context window


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import torch
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        DataCollatorWithPadding,
        EarlyStoppingCallback,
        Trainer,
        TrainingArguments,
    )

    train_rows = load_jsonl(ROOT / "data" / "train.jsonl")
    val_rows = load_jsonl(ROOT / "data" / "val.jsonl")
    print(f"train={len(train_rows)} val={len(val_rows)}")

    tokenizer = AutoTokenizer.from_pretrained(str(BASE_MODEL))

    class Dataset(torch.utils.data.Dataset):
        # No padding here: DataCollatorWithPadding pads per batch. DeBERTa-v2's
        # disentangled attention allocates O(batch * heads * seq^2) tensors, so
        # padding every sample to 512 blows up MPS memory for zero benefit.
        def __init__(self, rows):
            self.encodings = tokenizer(
                [r["text"] for r in rows],
                truncation=True,
                max_length=MAX_LEN,
            )
            self.labels = [r["label"] for r in rows]

        def __len__(self):
            return len(self.labels)

        def __getitem__(self, i):
            item = {k: v[i] for k, v in self.encodings.items()}
            item["labels"] = self.labels[i]
            return item

    # The base config carries no id2label; force a clean 2-label head mapping.
    model = AutoModelForSequenceClassification.from_pretrained(
        str(BASE_MODEL),
        num_labels=2,
        id2label={0: "BENIGN", 1: "MALICIOUS"},
        label2id={"BENIGN": 0, "MALICIOUS": 1},
    )

    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        tp = int(((preds == 1) & (labels == 1)).sum())
        fp = int(((preds == 1) & (labels == 0)).sum())
        fn = int(((preds == 0) & (labels == 1)).sum())
        tn = int(((preds == 0) & (labels == 0)).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {
            "accuracy": (tp + tn) / len(labels),
            "f1_malicious": f1,
            "recall_malicious": recall,
            "fpr": fp / (fp + tn) if fp + tn else 0.0,
        }

    targs = TrainingArguments(
        output_dir=str(OUT_DIR / "checkpoints"),
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=32,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        weight_decay=0.01,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="f1_malicious",
        greater_is_better=True,
        seed=args.seed,
        report_to=[],
        use_cpu=not torch.backends.mps.is_available(),
        save_total_limit=2,
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=Dataset(train_rows),
        eval_dataset=Dataset(val_rows),
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
    )
    trainer.train()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(OUT_DIR))
    tokenizer.save_pretrained(str(OUT_DIR))
    print(f"saved fine-tuned model to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
