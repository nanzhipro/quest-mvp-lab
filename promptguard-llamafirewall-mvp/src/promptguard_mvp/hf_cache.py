"""Seed the local model directory layout that LlamaFirewall expects.

LlamaFirewall's PromptGuard scanner loads the model from a *flat* directory::

    $HF_HOME/meta-llama--Llama-Prompt-Guard-2-86M/
        config.json
        model.safetensors
        tokenizer.json
        tokenizer_config.json
        special_tokens_map.json

(note: NOT the standard ``hub/`` snapshot layout — see
``llamafirewall/scanners/promptguard_utils.py``.)

The official repo is gated, so we hardlink the verified mirror files into a
project-local HF_HOME (``models/hf_home``) to keep everything self-contained
inside this MVP directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from .guard import MODEL_DIR

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_HF_HOME = PROJECT_ROOT / "models" / "hf_home"
LLAMAFIREWALL_MODEL_DIRNAME = "meta-llama--Llama-Prompt-Guard-2-86M"

REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
)


def seed_llamafirewall_model_dir(model_dir: Path = MODEL_DIR) -> Path:
    """Hardlink the mirror files into the flat dir LlamaFirewall looks for."""
    target = PROJECT_HF_HOME / LLAMAFIREWALL_MODEL_DIRNAME
    if all((target / name).exists() for name in REQUIRED_FILES):
        return target
    target.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES:
        src = model_dir / name
        if not src.exists():
            raise FileNotFoundError(
                f"missing {src}; run `uv run python scripts/download_model.py` first"
            )
        dst = target / name
        if dst.exists():
            dst.unlink()
        os.link(src, dst)
    return target


def ensure_hf_home() -> Path:
    """Point HF_HOME at the project-local cache and seed the model dir.

    Must run before LlamaFirewall/PromptGuardScanner is instantiated.
    """
    os.environ["HF_HOME"] = str(PROJECT_HF_HOME)
    seed_llamafirewall_model_dir()
    return PROJECT_HF_HOME
