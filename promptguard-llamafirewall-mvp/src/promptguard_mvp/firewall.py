"""LlamaFirewall integration for the ADR agent scenario.

Two scan surfaces, matching the threat model:
  - Role.USER  -> direct jailbreak attempts in user messages
  - Role.TOOL  -> indirect prompt injection buried in tool / MCP results

Important implementation note: ``LlamaFirewall.scan()`` constructs a fresh
``PromptGuardScanner`` per call, which reloads the 86M model every time
(see ``llamafirewall.llamafirewall.create_scanner``). For anything beyond a
one-shot demo, instantiate ``PromptGuardScanner`` once and reuse it — this
module's :class:`FirewallGate` does exactly that while keeping the official
ScanResult/ScanDecision types.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .hf_cache import ensure_hf_home

DEFAULT_BLOCK_THRESHOLD = 0.9  # LlamaFirewall's own PromptGuardScanner default


@dataclass
class FirewallVerdict:
    decision: str  # "allow" | "block"
    score: float
    reason: str

    @property
    def blocked(self) -> bool:
        return self.decision == "block"


class FirewallGate:
    """Reusable LlamaFirewall PromptGuard gate for user + tool messages."""

    def __init__(self, block_threshold: float = DEFAULT_BLOCK_THRESHOLD) -> None:
        ensure_hf_home()
        from llamafirewall.scanners.prompt_guard_scanner import PromptGuardScanner

        self._scanner = PromptGuardScanner(block_threshold=block_threshold)

    def scan(self, content: str, role: str = "user") -> FirewallVerdict:
        """Scan one message. role: "user" | "tool"."""
        from llamafirewall import ToolMessage, UserMessage

        message = ToolMessage(content=content) if role == "tool" else UserMessage(content=content)
        result = asyncio.run(self._scanner.scan(message))
        return FirewallVerdict(
            decision=result.decision.value,
            score=result.score,
            reason=result.reason,
        )
