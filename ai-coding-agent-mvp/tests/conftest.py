"""Shared test fixtures."""

from typing import Any, Dict, List


class FakeClient:
    """Scripted ChatClient double: pops one normalized response per chat() call."""

    def __init__(self, script: List[Dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages: List[Dict], tools: Any = None, **kwargs: Any) -> Dict:
        # Shallow-copy: the agent mutates the same list object after the call returns.
        self.calls.append({"messages": list(messages), "tools": tools})
        item = dict(self.script.pop(0))
        default = {
            "content": "",
            "reasoning": "",
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {},
        }
        default.update(item)
        return default


def tool_response(name: str, arguments: Dict, call_id: str = "call_1") -> Dict:
    return {
        "content": "",
        "reasoning": "",
        "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}],
        "finish_reason": "tool_calls",
        "usage": {},
    }
