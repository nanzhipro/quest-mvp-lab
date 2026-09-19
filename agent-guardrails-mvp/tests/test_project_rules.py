"""项目级不变量：让"可分享、可信、零依赖"这些说法变成会失败的断言。

规则写在测试里而不是文档里，因为文档不会让构建变红。
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
except AttributeError:  # pragma: no cover - 仅 3.9
    STDLIB = set()

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
PACKAGE = SOURCE / "agent_guardrails"

# 分片拼接，避免这个文件自己成为扫描目标。
ENTERPRISE_TOKENS = [
    "\u957f\u4ead",
    "\u85ae\u732b",
    "cyberser" + "val",
    "chai" + "tin",
    "CCLJ2G" + "NM3D",
    "serval" + "tech",
]
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
    "open" + "ai",
    "anthropic",
    "httpx",
    "requests",
    "guard" + "rails",
    "nemo" + "_guardrails",
]
# 不允许出现 print 的核心模块：日志走 obs，输出走 CLI。
NO_PRINT_MODULES = ("runtime.py", "gate.py", "tools.py", "budget.py", "guardrails.py", "audit.py", "llm.py")


def source_files() -> List[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def all_project_files() -> List[Path]:
    skip = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "runs"}
    return [
        path
        for path in PROJECT.rglob("*")
        if path.is_file() and not any(part in skip for part in path.relative_to(PROJECT).parts)
    ]


def test_no_agent_framework_is_imported() -> None:
    offenders: List[str] = []
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        for name in FRAMEWORK_IMPORTS:
            if re.search(r"^\s*(import|from)\s+{}\b".format(re.escape(name)), text, re.MULTILINE):
                offenders.append("{}: {}".format(path.name, name))
    assert offenders == [], "禁止的依赖：{}".format(offenders)


@pytest.mark.skipif(not STDLIB, reason="sys.stdlib_module_names 需要 Python 3.10+")
def test_every_source_import_is_standard_library() -> None:
    external: Set[str] = set()
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                external.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                external.add(node.module.split(".")[0])
    external -= {"agent_guardrails"}
    assert external <= STDLIB, "非标准库导入：{}".format(sorted(external - STDLIB))


def test_runtime_dependencies_are_empty() -> None:
    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    assert "requires-python" in pyproject


def test_core_modules_do_not_print() -> None:
    """核心逻辑只发事件、不打印：这样同一份代码能在测试、CI、终端里跑。"""
    for name in NO_PRINT_MODULES:
        text = (PACKAGE / name).read_text(encoding="utf-8")
        assert "print(" not in text, "{} 不应直接打印".format(name)
        assert "sys.stdout" not in text, "{} 不应直接写 stdout".format(name)


def test_every_module_documents_itself() -> None:
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree) or ""
        assert len(docstring) > 80, "{} 缺少模块级说明（>80 字符）".format(path.name)


def test_no_enterprise_data_assets_in_the_project() -> None:
    offenders: List[str] = []
    for path in all_project_files():
        if path.suffix not in {".py", ".md", ".toml", ".json", ".txt", ".sh", ".cfg"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - 二进制
            continue
        for token in ENTERPRISE_TOKENS:
            if token in text:
                offenders.append("{}: {}".format(path.relative_to(PROJECT), token))
    assert offenders == [], "发现企业数据字样：{}".format(offenders)


def test_no_certificates_or_provisioning_profiles_are_committed() -> None:
    forbidden = {".p12", ".cer", ".mobileprovision", ".keystore", ".pem", ".key"}
    offenders = [str(path) for path in all_project_files() if path.suffix in forbidden]
    assert offenders == []


def test_logging_never_receives_the_api_key() -> None:
    """静态检查：源码里唯一引用 api_key 的地方是请求头构造函数。"""
    for name in ("runtime.py", "gate.py", "tools.py", "guardrails.py", "audit.py", "obs.py"):
        text = (PACKAGE / name).read_text(encoding="utf-8")
        assert "api_key" not in text, "{} 不应接触密钥".format(name)


def test_readme_documents_the_commands_the_cli_actually_offers() -> None:
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    parser_source = (PACKAGE / "cli.py").read_text(encoding="utf-8")
    for verb in ("run", "list", "probe", "verify"):
        assert 'sub.add_parser("{}"'.format(verb) in parser_source
        assert "guardrails {} ".format(verb) in readme or "guardrails {}".format(verb) in readme


def test_readme_and_spec_exist() -> None:
    for name in ("README.md", "SPEC.md"):
        assert (PROJECT / name).stat().st_size > 2000, "{} 内容过少".format(name)
