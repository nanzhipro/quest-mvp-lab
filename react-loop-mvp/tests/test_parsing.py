"""parsing.py — the text protocol reader, including the failure it must never accept."""

from __future__ import annotations

from react_loop_mvp.parsing import (
    Action,
    clean_argument,
    parse_reply,
    strip_hallucinated_observations,
)


def test_plain_action_is_parsed() -> None:
    outcome = parse_reply("Thought: 需要算一下\nAction: calculator\nAction Input: 3*(4+5)")
    assert outcome.kind == "action"
    assert outcome.action == Action(name="calculator", argument="3*(4+5)", raw="Action: calculator")
    assert outcome.thought == "需要算一下"


def test_full_width_colon_is_accepted() -> None:
    outcome = parse_reply("Thought: 检索\nAction：search_docs\nAction Input：目录级 AUTH_OPEN")
    assert outcome.action is not None
    assert (outcome.action.name, outcome.action.argument) == ("search_docs", "目录级 AUTH_OPEN")


def test_markdown_decorations_are_accepted() -> None:
    outcome = parse_reply("**Thought:** 想\n**Action:** `search_docs`\nAction Input: `AUTH_OPEN`")
    assert outcome.action is not None
    assert (outcome.action.name, outcome.action.argument) == ("search_docs", "AUTH_OPEN")


def test_inline_call_form_is_accepted() -> None:
    outcome = parse_reply("Thought: 直接算\nAction: calculator(2**10)")
    assert outcome.action is not None
    assert (outcome.action.name, outcome.action.argument) == ("calculator", "2**10")


def test_action_without_input_parses_but_flags_the_missing_argument() -> None:
    outcome = parse_reply("Thought: 我想检索\nAction: search_docs\n")
    assert outcome.action is not None
    assert outcome.action.has_argument is False


def test_action_input_on_a_later_line_skips_blank_lines() -> None:
    outcome = parse_reply("Action: calculator\n\n\nAction Input: 1+1")
    assert outcome.action is not None
    assert outcome.action.argument == "1+1"


def test_action_input_is_not_taken_from_a_second_action() -> None:
    outcome = parse_reply("Action: calculator\nAction: search_docs\nAction Input: x")
    assert outcome.action is not None
    assert outcome.action.name == "calculator"
    assert outcome.action.has_argument is False


def test_final_answer_wins_over_a_later_action() -> None:
    outcome = parse_reply("Thought: 够了\nFinal Answer: 42\nAction: calculator\nAction Input: 1+1")
    assert outcome.kind == "final_answer"
    assert outcome.final_answer == "42"


def test_final_answer_can_span_lines() -> None:
    outcome = parse_reply("Final Answer: 第一行\n第二行")
    assert outcome.final_answer == "第一行\n第二行"


def test_chinese_final_answer_keyword() -> None:
    outcome = parse_reply("最终答案：就是它")
    assert outcome.final_answer == "就是它"


def test_reply_with_neither_protocol_element_is_unparsed() -> None:
    outcome = parse_reply("我觉得应该先查一下文档")
    assert outcome.kind == "unparsed"
    assert outcome.action is None
    assert outcome.final_answer is None


def test_model_written_observation_is_cut_off() -> None:
    text = "Thought: 我先猜一个结果\nAction: calculator\nAction Input: 1+1\nObservation: 2\n"
    outcome = parse_reply(text)
    assert outcome.hallucinated_observation is True
    assert "Observation" not in outcome.text
    assert outcome.action is not None  # the action itself is still usable


def test_observation_before_the_action_also_truncates_the_reply() -> None:
    text = "Observation: 我编的\nAction: calculator\nAction Input: 1"
    outcome = parse_reply(text)
    assert outcome.hallucinated_observation is True
    assert outcome.action is None


def test_strip_hallucinated_observations_keeps_clean_text_intact() -> None:
    text = "Thought: 干净\nAction: calculator\nAction Input: 1"
    kept, truncated = strip_hallucinated_observations(text)
    assert kept == text
    assert truncated is False


def test_clean_argument_removes_wrapping_quotes_and_backticks() -> None:
    assert clean_argument('"AUTH_OPEN"') == "AUTH_OPEN"
    assert clean_argument("`2+2`") == "2+2"
    assert clean_argument("“目录级”") == "目录级"
    assert clean_argument("  ") is None
    assert clean_argument(None) is None


def test_thought_is_whitespace_normalised_and_unlabelled() -> None:
    outcome = parse_reply("Thought:   想一下   \nAction: calculator\nAction Input: 1")
    assert outcome.thought == "想一下"
