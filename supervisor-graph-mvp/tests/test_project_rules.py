"""Project rules — the guard suite that keeps the MVP honest.

Each rule here exists because breaking it would quietly invalidate a claim in the
README: a third-party import would make "stdlib only" false, an enterprise asset would
make the repo unshareable, a stale template dependency would make the deterministic
fallback produce a DAG the agents cannot honour, and undocumented node names would
drift away from the report. Failing one of these is a build error, by design.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from supervisor_graph_mvp.agents.registry import default_registry
from supervisor_graph_mvp.pipeline import NODE_HELP
from supervisor_graph_mvp.scenarios import load_scenarios
from supervisor_graph_mvp.supervisor import AGENT_REQUIRES, CANONICAL_PLAN, INTENT_TO_AGENT, INTENTS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src" / "supervisor_graph_mvp"
DATA = SRC / "data"
DOCS = [PROJECT_ROOT / "README.md", PROJECT_ROOT / "SPEC.md"]

FORBIDDEN_FRAMEWORKS = re.compile(
    r"\b(langchain|llamaindex|llama_index|langgraph|autogen|crewai|dspy|smolagents|"
    r"pydantic_ai|haystack|openai|anthropic|httpx|requests|aiohttp)\b"
)
ENTERPRISE_MARKERS = re.compile(
    r"(长亭|薮猫|cyberserval|chaitin|CCLJ2GNM3D|com\.cyberserval)", re.IGNORECASE
)
SECRET_SUFFIXES = (".p12", ".cer", ".pem", ".key", ".provisionprofile", ".mobileprovision", ".pfx")


def python_files() -> list:
    return sorted(SRC.rglob("*.py"))


# ── dependency and import rules ───────────────────────────────────────────────
def test_runtime_dependencies_are_empty():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    assert 'requires-python = ">=3.9"' in pyproject


def test_only_the_standard_library_is_imported():
    stdlib = set(sys.stdlib_module_names) | {"__future__", "supervisor_graph_mvp"}
    offenders = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in stdlib:
                        offenders.append("{}: {}".format(path.name, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".")[0]
                if root not in stdlib:
                    offenders.append("{}: {}".format(path.name, node.module))
    assert offenders == []


def test_no_agent_framework_or_http_sdk_is_imported():
    """Scan real imports, not prose: the README and docstrings legitimately *mention* them."""
    offenders = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            roots = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if FORBIDDEN_FRAMEWORKS.fullmatch(root):
                    offenders.append("{}: {}".format(path.name, root))
    assert offenders == []


def test_core_modules_do_not_print():
    for name in (
        "graph.py",
        "consistency.py",
        "state.py",
        "supervisor.py",
        "pipeline.py",
        "predicates.py",
    ):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "print(" not in text, name
        assert "sys.stdout" not in text, name


def test_every_module_documents_itself():
    for path in python_files() + sorted((PROJECT_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree) or ""
        assert len(docstring) > 80, "{} has no proper module docstring".format(path.name)


# ── shareability rules ────────────────────────────────────────────────────────
def test_no_enterprise_asset_is_committed():
    """This file itself defines the marker patterns, so it is not scanned."""
    offenders = []
    for path in list(PROJECT_ROOT.rglob("*")):
        if not path.is_file() or ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == Path(__file__).name:
            continue
        if path.suffix in {".png", ".jpg", ".pdf", ".dylib", ".a", ".7z"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if ENTERPRISE_MARKERS.search(text):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == []


def test_no_certificate_or_provisioning_file_is_committed():
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in PROJECT_ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SECRET_SUFFIXES
        and ".venv" not in path.parts
        and "__pycache__" not in path.parts
    ]
    assert offenders == []


def test_no_api_key_shaped_string_in_the_tree():
    pattern = re.compile(r"sk-[A-Za-z0-9]{16,}")
    offenders = []
    for path in [*python_files(), *DOCS, DATA / "demo_script.json"]:
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == []


# ── domain-data integrity ─────────────────────────────────────────────────────
def test_data_files_are_present_and_parse():
    for name, key in (
        ("detectors.json", "detectors"),
        ("policy_rules.json", "rules"),
        ("remediation_actions.json", "actions"),
        ("scenarios.json", "scenarios"),
    ):
        document = json.loads((DATA / name).read_text(encoding="utf-8"))
        assert isinstance(document.get(key), list) and document[key], name
    scripts = json.loads((DATA / "demo_script.json").read_text(encoding="utf-8"))["scripts"]
    assert isinstance(scripts, dict) and scripts  # keyed by scenario id, not a flat list


def test_every_scenario_declares_a_real_asset_with_a_matching_digest():
    import hashlib

    for scenario in load_scenarios():
        asset = scenario["asset"]
        path = DATA / "assets" / asset["path"]
        assert path.is_file(), scenario["id"]
        for item in scenario["evidence"]:
            if item["kind"] != "artifact":
                continue
            name = item["id"].split(":", 1)[1]
            candidate = DATA / "assets" / name
            if not candidate.is_file():
                continue  # the missing-evidence scenario deliberately references one
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            assert item["sha256"] == digest, "{} digest drifted".format(item["id"])


def test_scenarios_exercise_every_graph_path():
    ids = {str(scenario["id"]) for scenario in load_scenarios()}
    assert {
        "S1-public-allow",
        "S2-plan-gap",
        "S3-missing-evidence",
        "S4-block-p4",
        "S5-plan-missing-agent",
    } == ids
    notes = " ".join(str(scenario.get("notes") or "") for scenario in load_scenarios())
    assert "修复" in notes and "证据" in notes


def test_every_scenario_has_a_scripted_replay():
    from supervisor_graph_mvp.demo import load_script, scripted_scenarios

    names = set(scripted_scenarios())
    assert {str(scenario["id"]) for scenario in load_scenarios()} <= names
    for name in names:
        assert load_script(name)


def test_detector_levels_are_within_the_declared_order():
    order = json.loads((DATA / "policy_rules.json").read_text(encoding="utf-8"))["level_order"]
    detectors = json.loads((DATA / "detectors.json").read_text(encoding="utf-8"))["detectors"]
    assert all(detector["level"] in order for detector in detectors)


# ── documentation / code agreement ────────────────────────────────────────────
def test_markdown_documents_exist_and_are_wired():
    for path in DOCS:
        assert path.is_file(), path.name
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    spec = (PROJECT_ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert "SPEC.md" in readme
    assert "README.md" in spec


def test_readme_names_every_graph_node_and_duty():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    for node in NODE_HELP:
        assert node in readme, node
    for duty in ("意图识别", "任务规划", "路由", "结果聚合"):
        assert duty in readme, duty


def test_spec_documents_every_node_and_intent():
    spec = (PROJECT_ROOT / "SPEC.md").read_text(encoding="utf-8")
    for node, purpose in NODE_HELP.items():
        assert node in spec
        assert purpose.split("（")[0] in spec
    for intent in INTENTS:
        assert intent in spec


def test_readme_lists_the_packaged_scenarios():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    for scenario in load_scenarios():
        assert str(scenario["id"]) in readme, scenario["id"]


def test_template_dependencies_match_the_agent_contracts():
    registry = default_registry()
    for row in registry.describe():
        assert tuple(AGENT_REQUIRES[row["name"]]) == tuple(row["requires"]), row["name"]
    ids = {other["id"]: other["agent"] for other in CANONICAL_PLAN}
    for task in CANONICAL_PLAN:
        assert {ids[dep] for dep in task["depends_on"]} <= set(AGENT_REQUIRES[task["agent"]])


def test_intent_to_agent_map_covers_every_tooling_intent():
    for intent in INTENTS:
        if intent == "compliance_ruling":
            continue
        assert intent in INTENT_TO_AGENT
    assert set(INTENT_TO_AGENT.values()) == set(AGENT_REQUIRES)


@pytest.mark.parametrize("path", ["pyproject.toml", ".gitignore"])
def test_project_scaffolding_files_exist(path: str):
    assert (PROJECT_ROOT / path).is_file()
