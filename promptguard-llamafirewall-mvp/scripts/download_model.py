#!/usr/bin/env python3
"""Download Llama-Prompt-Guard-2-86M from a public mirror and verify integrity.

The official ``meta-llama/Llama-Prompt-Guard-2-86M`` repo is gated (manual
license acceptance). This script pulls the byte-identical mirror
``project-free-llama/Llama-Prompt-Guard-2-86M`` and verifies every file
against the official Meta ``checklist.chk`` (md5sums) shipped in the repo.

Usage:
    uv run python scripts/download_model.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

MIRROR_REPO = "project-free-llama/Llama-Prompt-Guard-2-86M"
MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "Llama-Prompt-Guard-2-86M"
CHECKSUM_FILE = "checklist.chk"


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(model_dir: Path) -> list[str]:
    """Verify all files listed in checklist.chk. Returns a list of failures."""
    checklist = model_dir / CHECKSUM_FILE
    if not checklist.exists():
        return [f"missing {CHECKSUM_FILE}"]
    failures = []
    for line in checklist.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        expected, filename = line.split(None, 1)
        target = model_dir / filename
        if not target.exists():
            failures.append(f"missing {filename}")
            continue
        actual = md5sum(target)
        status = "OK" if actual == expected else "MISMATCH"
        print(f"  {status}  {filename}  md5={actual}")
        if actual != expected:
            failures.append(f"md5 mismatch: {filename}")
    return failures


def main() -> int:
    print(f"Downloading {MIRROR_REPO} -> {MODEL_DIR}")
    snapshot_download(
        repo_id=MIRROR_REPO,
        local_dir=str(MODEL_DIR),
        allow_patterns=["*.json", "*.safetensors", CHECKSUM_FILE, "LICENSE", "USE_POLICY.md"],
    )
    print("Verifying against official checklist.chk ...")
    failures = verify(MODEL_DIR)
    if failures:
        print("INTEGRITY CHECK FAILED:", *failures, sep="\n  ", file=sys.stderr)
        return 1
    print("All checksums OK. Model ready at", MODEL_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
