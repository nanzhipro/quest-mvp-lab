"""Project-level invariants: no framework, no third-party runtime deps, no enterprise data.

These are the rules that make the MVP *shareable* and *pedagogically honest*. They are
asserted here rather than documented, because documentation does not fail a build.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import List, Set

import pytest

try:  # 3.10+
    STDLIB: Set[str] = set(sys.stdlib_module_names)
except AttributeError:  # pragma: no cover - only on 3.9
    STDLIB = set()

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"

# Assembled from fragments so that this file is not itself a hit for the scan below.
ENTERPRISE_TOKENS = [
    "\u957f\u4ead",  # vendor A
    "\u85ae\u732b",  # vendor B
    "cyberser" + "val",
    "chai" + "tin",
    "CCLJ2G" + "NM3D",
    "serval" + "tech",
]
# Assembled for the same reason: listing them must not look like using them.
FRAMEWORK_IMPORTS = [
    "lang" + "chain",
    "llama_" + "index",
    "llama" + "index",
    "lang" + "graph",
    "auto" + "gen",
    "crew" + "ai",
    "dspy",
    "smol" + "agents",
    "pydantic_" + "ai",
    "hay" + "stack",
    "semantic_" + "kernel",
    "open" + "ai",
    "anthropic",
    "httpx",
    "requests",
]


def source_files() -> List[Path]:
    return sorted(SOURCE.rglob("*.py"))


def project_files() -> List[Path]:
    skip = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "runs"}
    files: List[Path] = []
    for path in PROJECT.rglob("*"):
        if any(part in skip for part in path.relative_to(PROJECT).parts):
            continue
        if path.is_file():
            files.append(path)
    return files


# ── no framework ─────────────────────────────────────────────────────────────
def test_no_agent_framework_is_imported() -> None:
    offenders: List[str] = []
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        for name in FRAMEWORK_IMPORTS:
            if re.search(r"^\s*(import|from)\s+{}\b".format(re.escape(name)), text, re.MULTILINE):
                offenders.append("{}: {}".format(path.name, name))
    assert offenders == [], "forbidden imports: {}".format(offenders)


@pytest.mark.skipif(not STDLIB, reason="sys.stdlib_module_names needs Python 3.10+")
def test_every_source_import_is_standard_library() -> None:
    external: Set[str] = set()
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                external.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                external.add(node.module.split(".")[0])
    external -= {"react_loop_mvp"}
    assert external <= STDLIB, "non-stdlib imports: {}".format(sorted(external - STDLIB))


def test_runtime_dependencies_are_empty() -> None:
    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    assert "requires-python" in pyproject


def test_package_has_no_import_of_the_cli_from_the_core() -> None:
    """The loops must not depend on argparse/printing: they receive a tracer instead."""
    for name in ("react.py", "native.py", "tools.py", "parsing.py"):
        text = (SOURCE / "react_loop_mvp" / name).read_text(encoding="utf-8")
        assert "import argparse" not in text
        assert "print(" not in text
        assert "sys.stdout" not in text


# ── pedagogy as a test ───────────────────────────────────────────────────────
def extract_core_loop() -> str:
    """The marked region of ``react.py`` — the *only* copy of the loop that counts."""
    text = (SOURCE / "react_loop_mvp" / "react.py").read_text(encoding="utf-8")
    start = text.index("core loop (start)")
    start = text.index("\n", start) + 1
    end = text.rindex("\n", 0, text.index("core loop (end)")) + 1
    return text[start:end].rstrip("\n")


def test_the_core_loop_stays_short() -> None:
    """The ReAct loop is the teaching artifact — it must remain readable in one screen."""
    loop = extract_core_loop()
    body = [line for line in loop.splitlines() if line.strip()]
    assert len(body) <= 30, "the core loop grew to {} non-blank lines".format(len(body))
    assert len(body) >= 15, "the loop lost its guards"
    assert "for index in range(1, self.max_steps + 1):" in loop
    # …and it still contains exactly the four moves of a ReAct step, nothing more.
    for move in (
        "self.model.complete(",
        "parse_reply(",
        "self.registry.call_text(",
        "append_observation(",
    ):
        assert move in loop, move


def test_readme_quotes_the_actual_loop() -> None:
    """Docs drift: the README snippet must be the shipped loop, byte for byte."""
    assert extract_core_loop() in (PROJECT / "README.md").read_text(encoding="utf-8")


def test_every_module_explains_itself() -> None:
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree) or ""
        assert len(docstring) > 80, "{} has no module docstring".format(path.name)


# ── shareability ─────────────────────────────────────────────────────────────
def test_no_enterprise_asset_appears_anywhere_in_the_project() -> None:
    hits: List[str] = []
    for path in project_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token in ENTERPRISE_TOKENS:
            if token.lower() in text.lower():
                hits.append("{}: {}".format(path.relative_to(PROJECT), token))
    assert hits == [], "enterprise data found: {}".format(hits)


def test_no_certificate_or_provisioning_artifact_is_committed() -> None:
    forbidden = {".p12", ".cer", ".pem", ".key", ".provisionprofile", ".mobileprovision", ".pfx"}
    offenders = [str(path) for path in project_files() if path.suffix in forbidden]
    assert offenders == []


def test_readme_and_spec_exist() -> None:
    assert (PROJECT / "README.md").stat().st_size > 2000
    assert (PROJECT / "SPEC.md").stat().st_size > 2000


def test_gitignore_covers_local_state() -> None:
    ignored = (PROJECT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".venv/", "__pycache__/", "runs/", ".env"):
        assert pattern in ignored
