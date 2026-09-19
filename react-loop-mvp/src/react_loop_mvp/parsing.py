"""The text protocol: what the model writes, and how strictly we read it.

This module *is* the framework. A tool-calling library hides exactly two jobs —
deciding when the model asked for a tool, and turning that ask into arguments —
and both live here, in about a hundred readable lines. The reader is deliberately
forgiving (full-width colons, code fences, stray quotes) and deliberately
defensive about one specific failure: **the model writing its own Observation**,
which, if accepted, injects fabricated tool output straight into the scratchpad.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# `Action:`, `Action：`, `**Action:**` — models decorate. Accept the decorations,
# keep the rule ("one action per reply") intact.
_ACTION_RE = re.compile(r"^\s*[\*`_]*(?:Action|动作)[\*`_]*\s*[:：]\s*(?P<rest>.+?)\s*[\*`_]*\s*$")
_ACTION_INPUT_RE = re.compile(
    r"^\s*[\*`_]*(?:Action\s*Input|ActionInput|行动输入|动作输入)[\*`_]*\s*[:：]\s*(?P<input>.*)$",
    re.IGNORECASE,
)
_INLINE_CALL_RE = re.compile(r"^(?P<name>[\w.\-]+)\s*[\(\[]\s*(?P<arg>.*?)\s*[\)\]]\s*$")
# Straight and typographic quotes / corner brackets — models wrap arguments in all of them.
_QUOTES = "\"'\u201c\u201d\u2018\u2019\u300c\u300d"
_FINAL_RE = re.compile(
    r"^\s*[\*`_]*(?:Final\s*Answer|最终答案|答案)[\*`_]*\s*[:：]\s*(?P<answer>.*)$",
    re.IGNORECASE,
)
_OBSERVATION_RE = re.compile(r"^\s*[\*`_]*(?:Observation|观察)[\*`_]*\s*[:：]", re.IGNORECASE)
_THOUGHT_PREFIX_RE = re.compile(
    r"^\s*[\*`_]*(?:Thought|思考|推理)[\*`_]*\s*[:：]\s*", re.IGNORECASE | re.MULTILINE
)


@dataclass(frozen=True)
class Action:
    """One parsed tool request: the name, and the raw single-string argument."""

    name: str
    argument: Optional[str]
    raw: str = ""

    @property
    def has_argument(self) -> bool:
        return self.argument is not None


@dataclass(frozen=True)
class ParseOutcome:
    """Result of reading one model reply."""

    action: Optional[Action]
    final_answer: Optional[str]
    thought: str
    text: str
    hallucinated_observation: bool

    @property
    def kind(self) -> str:
        if self.final_answer is not None:
            return "final_answer"
        if self.action is not None:
            return "action"
        return "unparsed"


def clean_argument(raw: Optional[str]) -> Optional[str]:
    """Strip the wrappers models add around an argument (quotes, backticks, marks)."""
    if raw is None:
        return None
    value = raw.strip()
    value = re.sub(r"^\s*[\*`_]+", "", value)
    value = re.sub(r"[\*`_]+\s*$", "", value)
    if len(value) >= 2 and value[0] in _QUOTES and value[-1] in _QUOTES:
        value = value[1:-1].strip()
    return value or None


def strip_hallucinated_observations(text: str) -> Tuple[str, bool]:
    """Cut everything from the first ``Observation:`` line onwards.

    The model is *only* allowed to think and to act; Observations belong to the
    environment. Models happily write ``Observation: ...`` themselves when they
    guess the tool output, and feeding that back into the scratchpad is the
    single most reliable way to make a ReAct agent lie to itself.
    """
    for index, line in enumerate(text.splitlines()):
        if _OBSERVATION_RE.match(line):
            kept = "\n".join(text.splitlines()[:index]).rstrip()
            return kept, True
    return text, False


def parse_reply(text: str) -> ParseOutcome:
    """Read one reply into at most one action, or a final answer.

    Precedence: ``Final Answer`` wins over ``Action`` (a reply that states the
    answer has stopped acting). ``Action`` without ``Action Input`` still parses —
    the loop turns that into a corrective Observation instead of a crash.
    """
    visible, hallucinated = strip_hallucinated_observations(text)
    lines = visible.splitlines()

    for index, line in enumerate(lines):
        match = _FINAL_RE.match(line)
        if match:
            answer_lines = [match.group("answer")]
            for follow in lines[index + 1 :]:
                if (
                    _ACTION_RE.match(follow)
                    or _ACTION_INPUT_RE.match(follow)
                    or _FINAL_RE.match(follow)
                ):
                    break
                answer_lines.append(follow)
            answer = _strip_marks("\n".join(answer_lines).strip())
            return ParseOutcome(
                action=None,
                final_answer=answer,
                thought=_thought_of(lines[:index]),
                text=visible,
                hallucinated_observation=hallucinated,
            )

    for index, line in enumerate(lines):
        match = _ACTION_RE.match(line)
        if not match:
            continue
        name, argument = _split_action(match.group("rest"))
        if argument is None:
            for follow in lines[index + 1 :]:
                input_match = _ACTION_INPUT_RE.match(follow)
                if input_match:
                    argument = clean_argument(input_match.group("input"))
                    break
                if _ACTION_RE.match(follow) or _FINAL_RE.match(follow):
                    break
                if follow.strip():
                    continue
        return ParseOutcome(
            action=Action(name=name, argument=argument, raw=line),
            final_answer=None,
            thought=_thought_of(lines[:index]),
            text=visible,
            hallucinated_observation=hallucinated,
        )

    return ParseOutcome(
        action=None,
        final_answer=None,
        thought=_thought_of(lines),
        text=visible,
        hallucinated_observation=hallucinated,
    )


def _split_action(rest: str) -> Tuple[str, Optional[str]]:
    """Accept both ``Action: name`` and ``Action: name(argument)`` spellings."""
    text = rest.strip()
    inline = _INLINE_CALL_RE.match(text)
    if inline:
        return inline.group("name").strip(), clean_argument(inline.group("arg"))
    return text.strip("`*_ ").strip(), None


def _thought_of(lines: List[str]) -> str:
    """The model's reasoning, without its own ``Thought:`` label (renderers add it)."""
    text = "\n".join(line.strip() for line in lines if line.strip()).strip()
    return _THOUGHT_PREFIX_RE.sub("", text, count=1).strip()


def _strip_marks(value: str) -> str:
    return re.sub(r"^[\*`_]+|[\*`_]+$", "", value.strip()).strip()
