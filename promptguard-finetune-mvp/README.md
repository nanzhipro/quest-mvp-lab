# promptguard-finetune-mvp

Fine-tuning Meta **PromptGuard 2 (86M)** on **LLM-generated synthetic data** to
fix the domain false positives observed in the ADR agent scenario — and to
validate end-to-end whether the "cloud teacher → local student" route works
for an offline endpoint product.

Companion to [`promptguard-llamafirewall-mvp`](../promptguard-llamafirewall-mvp/),
which established the baseline: base model = 100% attack recall but 25% FPR on
a 19-sample corpus, with both false positives being benign *security-discussion*
texts (`bn-03`, `bn-08`).

## Background

PromptGuard 2's official guidance is that the model should be fine-tuned on
application-specific data — its own model card states that benign/malicious
distributions differ per application and Meta's training pipeline itself uses
synthetic attack cases. The ADR agent scans two surfaces (user messages and
tool/MCP results) where security-adjacent documents are common, so the base
model's "discussion of injection == injection" confusion is a real cost.

This MVP tests the hypothesis with the smallest honest experiment:

1. Generate a labeled synthetic corpus with a strong cloud LLM (attack
   families × carriers × languages, plus hard negatives).
2. Full fine-tune the 86M model locally (Apple Silicon, no GPU budget).
3. Measure transfer on a corpus the training never saw, and iterate.

## Layout

```
├── pyproject.toml             # uv project: torch, transformers, accelerate, pytest
├── scripts/
│   ├── build_dataset.py       # schema validation, near-dedup, stratified split
│   ├── train.py               # full fine-tune (Trainer, MPS, early stop on F1)
│   └── eval_compare.py        # base vs finetuned: holdout + original corpus
├── data/
│   ├── seeds/*.jsonl          # LLM-authored batches (one attack family / negative class each)
│   ├── {train,val,test}.jsonl # built splits (stratified by category)
│   └── eval_corpus.json       # the 19-sample ADR corpus (never trained on)
├── tests/                     # dataset integrity + model regression (skip if untrained)
└── models/                    # gitignored: base model (hardlinked) + finetuned output
```

## Dataset design

Each row: `{"text", "label", "category", "lang", "carrier"}` — binary label
(1 = malicious), with the category axis doing the real work:

| Category | Label | Purpose |
| -------- | ----- | ------- |
| `jailbreak_direct` | 1 | Direct jailbreaks, EN+ZH, DAN/override/roleplay/encoding families |
| `indirect_injection` | 1 | Injection buried in tool output: JSON fields, emails, logs, code comments, markdown |
| `indirect_injection_subtle` | 1 | No "ignore instructions" phrasing: HTML comments, base64, conditional triggers, goal hijack |
| `benign_general` | 0 | Ordinary user requests and tool outputs — the "normal distribution" anchor |
| `benign_security_discussion` | 0 | **Hard negatives**: blogs/training/CTF writeups *about* injection — the FP class being fixed |
| `benign_instruction_lookalike` | 0 | Imperative verbs aimed at business objects ("ignore my last email") — teaches the AI-vs-world distinction |

## Build / run / verify

```bash
cd quest-mvp-lab/promptguard-finetune-mvp
uv sync
uv run python scripts/build_dataset.py     # validate + dedup + split seeds
uv run python scripts/train.py             # fine-tune (defaults: lr 2e-5, 3 epochs)
uv run python scripts/eval_compare.py      # base vs finetuned, holdout + corpus
uv run pytest                              # dataset integrity + model regression
```

## Key decisions and what they cost

- **Full fine-tune, not LoRA.** At 86M params, LoRA buys no meaningful memory
  savings; full FT is the honest baseline. (LoRA's real benefit — less
  forgetting — matters when you can't mix replay data; here we can.)
- **Dynamic padding.** DeBERTa-v2's disentangled attention allocates
  O(batch·heads·seq²); padding everything to 512 OOM'd MPS at ~20 GiB.
  `DataCollatorWithPadding` fixed it and roughly halves step time.
- **The original 19-sample corpus is never trained on.** It is the transfer
  benchmark — training on it would be teaching to the test.
- **Metrics are TPR/FPR, not accuracy**, per the official model card's
  guidance; threshold calibration is a separate, deployment-side decision.

## Results

1,487 synthetic samples (8 attack/benign batches + a 442-sample benign
top-up after v1's failure), full fine-tune on Apple Silicon MPS (~20 min),
evaluated on a stratified holdout and the never-trained-on 19-sample ADR
corpus.

| Version | Recipe | Holdout acc / TPR / FPR | ADR corpus acc / TPR / FPR |
| ------- | ------ | ----------------------- | -------------------------- |
| base | — | 59.0% / 15.6% / 7.9% | 89.5% / 100% / 25.0% |
| v1 | 1,046 rows, narrow benign, lr 2e-5 × 3ep | 97.6% / 98.7% / 4.2% | **78.9% / 100% / 50.0% — regressed** |
| v2 | +442 benign/replay rows, lr 1e-5 × 2ep | 89.9% / 80.5% / 3.0% | 94.7% / 100% / 12.5%; **100% at threshold 0.9** |
| v3 (shipped) | v2 data, lr 2e-5 × 2ep | 91.0% / 81.8% / 2.0% | 94.7% / 100% / 12.5% (bn-08 = 0.925, just over 0.9) |

What the runs teach:

- **The targeted fix works.** bn-03 (blog post about prompt injection) went
  from 0.999 malicious on the base model to **0.002** after fine-tuning; the
  whole `benign_security_discussion` class is 100% correct on v3's holdout.
- **v1 is the documented trap, live.** Fine-tuning on attack-heavy synthetic
  data with thin benign coverage made OOD benign texts *worse* (a plain
  "translate this" request scored 0.98 malicious). Widening benign coverage
  and lowering the LR fixed it — the poor man's substitute for Meta's
  energy-based OOD loss + replay.
- **Holdout scores flatter both directions.** The base model's 15.6% TPR on
  the holdout mostly means "unfamiliar with this synthetic distribution";
  read transfer from the never-trained-on corpus, not the holdout.
- **Threshold is a joint knob with training.** v2 reached 100% on the corpus
  only at 0.9; v3 nudged one sample back over the line. At 19 samples these
  differences are single-sample noise — production needs multi-seed runs and
  a ≥500-sample private eval set.

## Conclusion

The "cloud teacher → local student" route is **validated for the ADR
scenario**: one afternoon of LLM data generation plus a 20-minute local
fine-tune measurably improved the deployment-relevant metric (corpus FPR
25% → 12.5% at 0.5, 0% at 0.9 on v2) while keeping 100% attack recall, all
offline, at zero marginal cost. The known ceiling stands: published adaptive
attacks bypass this class of detector at >90% ASR, so the fine-tuned model is
a first-pass sieve feeding deterministic controls — not a verdict.
