"""tools.py — calculator safety, retrieval ranking, and the registry contract."""

from __future__ import annotations

from typing import List

import pytest

from react_loop_mvp.tools import (
    KnowledgeDoc,
    ToolRegistry,
    ToolSpec,
    calculate,
    default_registry,
    load_knowledge_base,
    score_document,
    search_documents,
    tokenize,
)


# ── calculator ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+1", "2"),
        ("3*(4+5)", "27"),
        ("2**10", "1024"),
        ("7/2", "3.5"),
        ("7//2", "3"),
        ("7%3", "1"),
        ("-(3+4)", "-7"),
        ("10-2-3", "5"),
        ("1.5*2", "3"),
        ("0.1+0.2", "0.3"),
        ("1e3", "1000"),
        ("9007199254740993", "9007199254740993"),
    ],
)
def test_calculator_evaluates_pure_arithmetic(expression: str, expected: str) -> None:
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "1/0",
        "open('/etc/passwd')",
        "__import__('os').system('echo hi')",
        "a + 1",
        "[1,2,3]",
        "'a' * 3",
        "1+",
        "lambda: 1",
        "print(1)",
    ],
)
def test_calculator_rejects_everything_that_is_not_arithmetic(expression: str) -> None:
    with pytest.raises((ValueError, ZeroDivisionError)):
        calculate(expression)


def test_calculator_refuses_a_huge_exponent() -> None:
    with pytest.raises(ValueError):
        calculate("2**9999")


def test_calculator_refuses_a_huge_base_times_a_big_exponent() -> None:
    with pytest.raises(ValueError):
        calculate("2e6**9")


def test_calculator_refuses_an_overlong_expression() -> None:
    with pytest.raises(ValueError):
        calculate("+".join(["1"] * 200))


def test_calculator_keeps_absolute_values_exact() -> None:
    assert calculate("9007199254740993 + 1") == "9007199254740994"


# ── retrieval ────────────────────────────────────────────────────────────────
def test_tokenize_keeps_latin_words_and_chinese_bigrams() -> None:
    assert tokenize("AUTH_OPEN") == ["auth_open"]
    assert tokenize("目录级") == ["目录", "录级"]
    assert tokenize("a") == []  # single latin characters carry no signal


def test_search_prefers_the_document_that_matches_the_phrase(docs: List[KnowledgeDoc]) -> None:
    rendered = search_documents("目录级 AUTH_OPEN", docs)
    assert rendered.splitlines()[1].startswith("[1] alpha |")


def test_search_resolves_the_second_hop_query_to_the_spec_document(
    docs: List[KnowledgeDoc],
) -> None:
    """The demo trajectory depends on this ranking — assert it explicitly."""
    rendered = search_documents("SPEC.md §9 结论", docs)
    assert "alpha-spec" in rendered.splitlines()[1]


def test_search_title_hits_outrank_body_hits() -> None:
    corpus = [
        KnowledgeDoc(id="body", title="杂项", text="延迟静音策略在正文里出现过一次。"),
        KnowledgeDoc(id="title", title="反向静音策略", text="与关键词无关的正文。"),
    ]
    rendered = search_documents("反向静音策略", corpus)
    assert rendered.splitlines()[1].startswith("[1] title |")


def test_search_is_deterministic_for_equal_scores() -> None:
    corpus = [
        KnowledgeDoc(id="bbb", title="AUTH_OPEN", text="x"),
        KnowledgeDoc(id="aaa", title="AUTH_OPEN", text="x"),
    ]
    first = search_documents("AUTH_OPEN", corpus)
    assert first == search_documents("AUTH_OPEN", corpus)
    assert first.splitlines()[1].startswith("[1] aaa |")  # ties break on id


def test_search_reports_no_match_without_inventing_a_document(
    docs: List[KnowledgeDoc],
) -> None:
    rendered = search_documents("量子纠缠测不准原理", docs)
    assert rendered.startswith("no document matched")
    assert "gamma" not in rendered


def test_search_respects_the_limit(docs: List[KnowledgeDoc]) -> None:
    rendered = search_documents("es", docs, limit=1)
    assert rendered.splitlines()[0] == "1 hit(s) of 4 documents:"


def test_search_rejects_an_empty_query(docs: List[KnowledgeDoc]) -> None:
    with pytest.raises(ValueError):
        search_documents("   ", docs)


def test_score_is_zero_when_nothing_matches(docs: List[KnowledgeDoc]) -> None:
    assert score_document("完全无关的词", docs[0]) == 0.0


def test_search_truncates_long_bodies(docs: List[KnowledgeDoc]) -> None:
    corpus = [KnowledgeDoc(id="long", title="AUTH_OPEN", text="x" * 900)]
    rendered = search_documents("AUTH_OPEN", corpus)
    assert "…" in rendered
    assert len(rendered) < 900


# ── registry ─────────────────────────────────────────────────────────────────
def test_default_registry_exposes_two_tools(registry: ToolRegistry) -> None:
    assert registry.names() == ["calculator", "search_docs"]
    assert registry.has("search_docs")
    assert not registry.has("nope")


def test_catalog_lines_carry_the_parameter_name(registry: ToolRegistry) -> None:
    catalog = registry.catalog()
    assert "calculator[expression]" in catalog
    assert "search_docs[query]" in catalog


def test_json_schema_matches_the_text_protocol_parameter(registry: ToolRegistry) -> None:
    schemas = {entry["function"]["name"]: entry for entry in registry.json_schemas()}
    assert set(schemas) == {"calculator", "search_docs"}
    calculator = schemas["calculator"]
    assert calculator["type"] == "function"
    assert calculator["function"]["parameters"]["required"] == ["expression"]
    assert calculator["function"]["parameters"]["properties"]["expression"]["type"] == "string"


def test_call_text_runs_the_handler(registry: ToolRegistry) -> None:
    result = registry.call_text("calculator", "2+2")
    assert (result.ok, result.output) == (True, "4")


def test_call_text_reports_an_unknown_tool_without_raising(registry: ToolRegistry) -> None:
    result = registry.call_text("shell", "rm -rf /")
    assert result.ok is False
    assert "unknown tool 'shell'" in result.output
    assert "calculator" in result.output  # the model is told what does exist


def test_call_text_reports_a_missing_action_input(registry: ToolRegistry) -> None:
    result = registry.call_text("search_docs", None)
    assert result.ok is False
    assert "Action Input" in result.output


def test_tool_exception_becomes_an_observation(registry: ToolRegistry) -> None:
    result = registry.call_text("calculator", "1/0")
    assert result.ok is False
    assert "ZeroDivisionError" in result.output


def test_call_native_reads_the_named_argument(registry: ToolRegistry) -> None:
    result = registry.call_native("calculator", {"expression": "6*7"})
    assert (result.ok, result.output) == (True, "42")


def test_call_native_tolerates_a_single_misnamed_argument(registry: ToolRegistry) -> None:
    result = registry.call_native("search_docs", {"keyword": "AUTH_OPEN"})
    assert result.ok is True


def test_call_native_reports_missing_arguments(registry: ToolRegistry) -> None:
    result = registry.call_native("calculator", {})
    assert result.ok is False
    assert "expression" in result.output


def test_registry_caps_observation_length() -> None:
    spec = ToolSpec(
        name="noisy",
        summary="returns a lot",
        param_name="input",
        param_description="anything",
        handler=lambda _: "y" * 500,
    )
    registry = ToolRegistry([spec], max_output_chars=10)
    result = registry.call_text("noisy", "go")
    assert result.ok is True
    assert result.output == "yyyyyyyyyy…[truncated 490 chars]"


def test_registry_rejects_duplicate_names(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError):
        registry.register(
            ToolSpec(
                name="calculator",
                summary="dup",
                param_name="x",
                param_description="y",
                handler=lambda _: "",
            )
        )


# ── bundled corpus (a data contract the offline demo depends on) ─────────────
def test_bundled_knowledge_base_is_well_formed() -> None:
    docs = load_knowledge_base()
    assert len(docs) >= 5
    for doc in docs:
        assert doc.id and doc.title and doc.text
        assert doc.tags, "every document needs tags for the ranking to work"


def test_default_registry_answers_from_the_bundled_corpus() -> None:
    registry = default_registry()
    result = registry.call_text("search_docs", "目录级 AUTH_OPEN")
    assert result.ok is True
    assert result.output.splitlines()[1].startswith("[1] es-mvp |")


def test_bundled_corpus_can_resolve_the_spec_document() -> None:
    registry = default_registry()
    result = registry.call_text("search_docs", "SPEC.md §9 结论")
    assert result.output.splitlines()[1].startswith("[1] es-mvp-spec-9 |")
