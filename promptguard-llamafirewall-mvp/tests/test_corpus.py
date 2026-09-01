"""Model-free sanity tests: corpus integrity and CLI plumbing."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_corpus_integrity() -> None:
    corpus = json.loads((ROOT / "data" / "corpus.json").read_text())["samples"]
    ids = [s["id"] for s in corpus]
    assert len(ids) == len(set(ids)), "duplicate sample ids"
    for sample in corpus:
        assert sample["category"] in {"jailbreak", "indirect_injection", "benign"}
        assert sample["surface"] in {"user", "tool"}
        assert isinstance(sample["expect_malicious"], bool)
        assert sample["text"].strip()
    # both attack classes and benign must be represented
    categories = {s["category"] for s in corpus}
    assert categories == {"jailbreak", "indirect_injection", "benign"}
    assert any(s["expect_malicious"] for s in corpus)
    assert any(not s["expect_malicious"] for s in corpus)


def test_cli_help() -> None:
    from promptguard_mvp.cli import build_parser

    parser = build_parser()
    assert parser.prog == "pgscan"
