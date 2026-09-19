"""Tools: one registry, two protocols.

A tool here is exactly three things — a name, a one-line summary the model reads,
and a handler ``str -> str``. Both protocols call the *same* handler, which is why
the ReAct-vs-native comparison later in the README is honest: only the wire
format changes, never the tool behaviour.

Deliberate constraints:

* **One string parameter per tool.** The text protocol cannot express typed
  arguments, so the registry refuses to pretend otherwise; native mode wraps the
  same single parameter in a JSON schema. Same information, two encodings.
* **Handlers never raise through the registry.** A tool failure is an
  *Observation* ("tool error: ..."), which is information the model can act on,
  not an exception that kills the run.
* **Every observation is capped** (``max_output_chars``) — an uncapped tool
  output is how a ReAct scratchpad explodes into a context-length error.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Callable, Dict, List, Optional, Sequence

MAX_OUTPUT_CHARS = 2000
MAX_EXPRESSION_CHARS = 200
MAX_ABS_EXPONENT = 64
POWER_OVERFLOW_LIMIT = 1e6


# ── tool contract ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ToolSpec:
    """A tool's public face. ``handler`` is the whole implementation."""

    name: str
    summary: str
    param_name: str
    param_description: str
    handler: Callable[[str], str] = field(repr=False, compare=False)

    def catalog_line(self) -> str:
        """The line the ReAct prompt shows for this tool."""
        return "- {0}[{1}]: {2}".format(self.name, self.param_name, self.summary)

    def json_schema(self) -> Dict[str, Any]:
        """The same tool, described the way native function calling wants it."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.summary,
                "parameters": {
                    "type": "object",
                    "properties": {
                        self.param_name: {"type": "string", "description": self.param_description}
                    },
                    "required": [self.param_name],
                },
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool invocation — always renderable as an Observation."""

    tool: str
    argument: Optional[str]
    output: str
    ok: bool

    def as_observation(self) -> str:
        return self.output


class ToolRegistry:
    """Name → spec, plus the two invocation paths and their error vocabulary."""

    def __init__(self, specs: Sequence[ToolSpec] = (), *, max_output_chars: int = MAX_OUTPUT_CHARS):
        self._specs: Dict[str, ToolSpec] = {}
        self.max_output_chars = max_output_chars
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError("duplicate tool name: {}".format(spec.name))
        self._specs[spec.name] = spec

    def names(self) -> List[str]:
        return sorted(self._specs)

    def specs(self) -> List[ToolSpec]:
        return [self._specs[name] for name in self.names()]

    def has(self, name: str) -> bool:
        return name in self._specs

    def catalog(self) -> str:
        """The tool list embedded in the ReAct prompt."""
        return "\n".join(spec.catalog_line() for spec in self.specs())

    def json_schemas(self) -> List[Dict[str, Any]]:
        """``tools=[...]`` payload for native function calling."""
        return [spec.json_schema() for spec in self.specs()]

    # ── invocation ───────────────────────────────────────────────────────────
    def call_text(self, name: str, argument: Optional[str]) -> ToolResult:
        """ReAct path: ``Action: name`` + ``Action Input: <string>``."""
        spec = self._specs.get(name)
        if spec is None:
            return self._fail(
                name,
                argument,
                "unknown tool '{}'; available: {}".format(name, ", ".join(self.names())),
            )
        if argument is None:
            return self._fail(
                name, argument, "tool '{}' needs an Action Input ({})".format(name, spec.param_name)
            )
        return self._invoke(spec, argument, argument)

    def call_native(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Native path: ``tool_calls[i].function.arguments`` as a JSON object."""
        spec = self._specs.get(name)
        if spec is None:
            return self._fail(
                name, None, "unknown tool '{}'; available: {}".format(name, ", ".join(self.names()))
            )
        raw = arguments.get(spec.param_name)
        if raw is None and len(arguments) == 1:
            raw = next(iter(arguments.values()))  # tolerate a mis-named single argument
        if raw is None:
            return self._fail(
                name,
                None,
                "tool '{}' needs argument '{}' (got: {})".format(
                    name, spec.param_name, ", ".join(sorted(arguments)) or "nothing"
                ),
            )
        return self._invoke(spec, str(raw), arguments)

    def _invoke(self, spec: ToolSpec, argument: str, reported: Any) -> ToolResult:
        try:
            output = spec.handler(argument)
        except Exception as exc:  # a tool error is data the model can act on, not a crash
            return self._fail(
                spec.name,
                argument,
                "tool '{}' failed: {}: {}".format(spec.name, type(exc).__name__, exc),
            )
        return ToolResult(
            tool=spec.name,
            argument=argument if isinstance(reported, str) else str(reported),
            output=self._cap(output),
            ok=True,
        )

    def _fail(self, name: str, argument: Optional[str], message: str) -> ToolResult:
        return ToolResult(tool=name, argument=argument, output=message, ok=False)

    def _cap(self, output: str) -> str:
        if len(output) <= self.max_output_chars:
            return output
        return "{}…[truncated {} chars]".format(
            output[: self.max_output_chars], len(output) - self.max_output_chars
        )


# ── tool 1: calculator (AST-based, never eval) ───────────────────────────────
_BIN_OPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: Dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def calculate(expression: str) -> str:
    """Evaluate pure arithmetic. ``eval`` is never used: only a whitelisted AST."""
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ValueError("expression longer than {} chars".format(MAX_EXPRESSION_CHARS))
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("not a valid arithmetic expression: {!r}".format(expression)) from exc
    value = _eval_node(tree.body)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("result is not finite")
    return _format_number(value)


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (
            abs(right) > MAX_ABS_EXPONENT or (abs(left) > POWER_OVERFLOW_LIMIT and right > 8)
        ):
            raise ValueError("power too large; keep |exponent| <= {}".format(MAX_ABS_EXPONENT))
        return _BIN_OPS[type(node.op)](left, right)
    raise ValueError("unsupported syntax: {}".format(type(node).__name__))


def _format_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, float):
        return repr(round(value, 10))
    return str(value)


# ── tool 2: local document search ────────────────────────────────────────────
@dataclass(frozen=True)
class KnowledgeDoc:
    id: str
    title: str
    text: str
    tags: Sequence[str] = ()

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "KnowledgeDoc":
        return cls(
            id=str(raw["id"]),
            title=str(raw["title"]),
            text=str(raw["text"]),
            tags=[str(tag) for tag in raw.get("tags", [])],
        )


def tokenize(text: str) -> List[str]:
    """CJK-aware tokenisation: latin words plus Chinese character bigrams."""
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9_]{2,}", lowered)
    han = re.findall(r"[\u4e00-\u9fff]", lowered)
    if len(han) > 1:
        tokens.extend("".join(pair) for pair in zip(han, han[1:]))
    else:
        tokens.extend(han)
    return tokens


def score_document(query: str, doc: KnowledgeDoc) -> float:
    """Title hits are worth 3, tag hits 2, body hits 1; exact phrase gets a bonus."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.0
    title_tokens = set(tokenize(doc.title))
    tag_tokens = set(tokenize(" ".join(doc.tags)))
    body_tokens = set(tokenize(doc.text))
    score = 0.0
    for token in query_tokens:
        if token in title_tokens:
            score += 3.0
        if token in tag_tokens:
            score += 2.0
        if token in body_tokens:
            score += 1.0
    phrase = query.strip().lower()
    if len(phrase) >= 4 and phrase in doc.text.lower():
        score += 5.0
    return score


def search_documents(query: str, docs: Sequence[KnowledgeDoc], *, limit: int = 3) -> str:
    """Rank the corpus for ``query`` and render a compact, model-readable result."""
    needle = query.strip()
    if not needle:
        raise ValueError("query is empty")
    ranked = sorted(
        ((score_document(needle, doc), doc) for doc in docs),
        key=lambda pair: (-pair[0], pair[1].id),
    )
    hits = [pair for pair in ranked if pair[0] > 0][:limit]
    if not hits:
        return "no document matched {!r} ({} documents searched); try another keyword".format(
            needle, len(docs)
        )
    lines = ["{} hit(s) of {} documents:".format(len(hits), len(docs))]
    for rank, (score, doc) in enumerate(hits, start=1):
        body = doc.text if len(doc.text) <= 400 else doc.text[:400] + "…"
        lines.append("[{0}] {1} | {2} | score {3:.1f}".format(rank, doc.id, doc.title, score))
        lines.append("    {}".format(body))
    return "\n".join(lines)


def load_knowledge_base() -> List[KnowledgeDoc]:
    """Load the bundled corpus (``data/kb.json``) shipped inside the package."""
    raw = resources.files("react_loop_mvp").joinpath("data/kb.json").read_text(encoding="utf-8")
    import json

    return [KnowledgeDoc.from_dict(entry) for entry in json.loads(raw)]


# ── the default tool set ─────────────────────────────────────────────────────
def default_registry(docs: Optional[Sequence[KnowledgeDoc]] = None) -> ToolRegistry:
    """Calculator + document search, both deterministic and network-free."""
    corpus = list(docs) if docs is not None else load_knowledge_base()
    return ToolRegistry(
        [
            ToolSpec(
                name="calculator",
                summary="计算一个纯算术表达式（支持 + - * / // % ** 与括号）",
                param_name="expression",
                param_description="例如 (3+4)*5 或 2**10",
                handler=calculate,
            ),
            ToolSpec(
                name="search_docs",
                summary="在本地文档库中检索（返回命中的文档 id、标题与正文片段）",
                param_name="query",
                param_description="检索关键词，例如 目录级 AUTH_OPEN",
                handler=lambda query: search_documents(query, corpus),
            ),
        ]
    )
