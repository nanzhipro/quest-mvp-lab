"""Thin wrapper around the local Llama-Prompt-Guard-2-86M classifier.

PromptGuard 2 (86M) is a DeBERTa-v2 binary sequence classifier:
  - label index 0 -> BENIGN
  - label index 1 -> MALICIOUS (jailbreak / prompt injection)

It is designed to scan two kinds of content in an agent pipeline:
  1. user messages (direct jailbreak attempts)
  2. tool / MCP results (indirect prompt injection buried in tool output)

The model accepts at most 512 tokens; longer inputs are truncated by the
tokenizer (matching the reference usage on the model card).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "Llama-Prompt-Guard-2-86M"

# DeBERTa-v2 config carries no id2label mapping; per the model card,
# index 0 = benign, index 1 = malicious (injection/jailbreak combined).
LABEL_BENIGN = "BENIGN"
LABEL_MALICIOUS = "MALICIOUS"


@dataclass(frozen=True)
class GuardVerdict:
    """Result of scanning one piece of text."""

    label: str
    score: float  # probability of the predicted label
    malicious_score: float  # probability of MALICIOUS regardless of prediction
    latency_ms: float
    truncated: bool

    @property
    def is_malicious(self) -> bool:
        return self.label == LABEL_MALICIOUS


@dataclass
class PromptGuard:
    """Lazy-loading local PromptGuard 2 classifier.

    Device selection: MPS (Apple Silicon) if available, else CPU.
    """

    model_dir: Path = MODEL_DIR
    device: str | None = None
    threshold: float = 0.5  # malicious probability above this -> MALICIOUS
    _pipeline: object | None = field(default=None, init=False, repr=False)

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

        if not self.model_dir.exists():
            raise FileNotFoundError(
                f"model not found at {self.model_dir}; run `uv run python scripts/download_model.py`"
            )
        device = self.device
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        model = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir))
        self._pipeline = pipeline(
            "text-classification",
            model=model,
            tokenizer=tokenizer,
            device=device,
            truncation=True,
            max_length=512,
            top_k=None,  # return scores for all labels
        )
        self.device = device
        return self._pipeline

    def scan(self, text: str) -> GuardVerdict:
        """Classify one text; returns a GuardVerdict."""
        clf = self._load()
        tokenizer = clf.tokenizer
        n_tokens = len(tokenizer.encode(text))
        start = time.perf_counter()
        raw = clf(text)[0]  # list of {label, score} for both classes
        latency_ms = (time.perf_counter() - start) * 1000
        scores = {entry["label"]: entry["score"] for entry in raw}
        # With no id2label in config.json the pipeline emits LABEL_0 / LABEL_1.
        malicious_score = scores.get("LABEL_1", scores.get(LABEL_MALICIOUS, 0.0))
        label = LABEL_MALICIOUS if malicious_score >= self.threshold else LABEL_BENIGN
        return GuardVerdict(
            label=label,
            score=malicious_score if label == LABEL_MALICIOUS else 1.0 - malicious_score,
            malicious_score=malicious_score,
            latency_ms=latency_ms,
            truncated=n_tokens > 512,
        )

    def scan_batch(self, texts: list[str]) -> list[GuardVerdict]:
        return [self.scan(t) for t in texts]
