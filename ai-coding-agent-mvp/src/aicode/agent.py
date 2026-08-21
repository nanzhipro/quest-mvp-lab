"""The agent loop: alternate model turns and tool executions until done."""

import json
import os
from typing import Callable, Dict, List, Optional

from .tools import TOOL_SPECS, ToolRunner

SYSTEM_PROMPT = """You are a coding agent operating on a local machine.

Working directory: {workdir}

Rules:
- Solve the user's task by inspecting and modifying files with your tools.
- Prefer acting over explaining: keep reasoning short, never paste code into chat
  replies — always deliver code through write_file.
- Prefer small, verifiable steps: read before you edit, then run a command to verify.
- Write complete files (never truncated placeholders) when asked to create code.
- Keep responses concise. Report what you changed with file paths.
- If a tool fails, read the error, adjust, and retry once before giving up.
- The task is finished when the user's request is satisfied and verified; end with a
  plain-text summary in the user's language.
"""


def assistant_message(resp: Dict) -> Dict:
    """Convert a normalized client response into an API-history assistant message."""
    msg: Dict = {"role": "assistant", "content": resp.get("content") or ""}
    tool_calls = resp.get("tool_calls") or []
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
    return msg


class Agent:
    """One-shot agent: runs a single task to completion."""

    def __init__(
        self,
        client,
        runner: ToolRunner,
        workdir: str,
        max_iters: int = 20,
        show_reasoning: bool = False,
        verbose: bool = False,
        on_content: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.client = client
        self.runner = runner
        self.workdir = os.path.abspath(workdir)
        self.max_iters = max_iters
        self.show_reasoning = show_reasoning
        self.verbose = verbose
        self.on_content = on_content

    def system_message(self) -> Dict:
        return {"role": "system", "content": SYSTEM_PROMPT.format(workdir=self.workdir)}

    def run_task(self, task: str) -> str:
        """Execute one task and return the model's final answer."""
        messages = [self.system_message(), {"role": "user", "content": task}]
        return self._loop(messages)

    def _loop(self, messages: List[Dict]) -> str:
        for iteration in range(self.max_iters):
            resp = self.client.chat(messages, tools=TOOL_SPECS, on_content=self.on_content)
            self._display(resp, iteration)
            messages.append(assistant_message(resp))

            tool_calls = resp.get("tool_calls") or []
            if not tool_calls:
                return resp.get("content") or ""

            results = []
            for tc in tool_calls:
                result = self.runner.dispatch(tc["name"], tc["arguments"])
                results.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                if self.verbose:
                    preview = result.replace("\n", " ")[:200]
                    print("  -> {}".format(preview))
            messages.extend(results)

        # Budget exhausted: force a tool-free final answer.
        messages.append(
            {
                "role": "user",
                "content": (
                    "Iteration budget exhausted. Summarize what is done and what remains, "
                    "without using tools."
                ),
            }
        )
        resp = self.client.chat(messages, tools=None, on_content=self.on_content)
        return resp.get("content") or ""

    def _display(self, resp: Dict, iteration: int) -> None:
        if self.verbose:
            usage = resp.get("usage") or {}
            print(
                "[iter {}] tokens in={} out={}".format(
                    iteration + 1, usage.get("prompt_tokens"), usage.get("completion_tokens")
                )
            )
        reasoning = resp.get("reasoning") or ""
        if reasoning and self.show_reasoning:
            print("\n[think]\n{}\n".format(reasoning))
        for tc in resp.get("tool_calls") or []:
            args = json.dumps(tc["arguments"], ensure_ascii=False)
            if len(args) > 160:
                args = args[:160] + "..."
            print("[{}] tool {} {}".format(iteration + 1, tc["name"], args))


class AgentSession:
    """Multi-turn variant used by the interactive REPL; keeps full history."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.messages: List[Dict] = [agent.system_message()]

    def turn(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        return self.agent._loop(self.messages)
