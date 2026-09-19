"""The specialist registry: the set of agents the Supervisor is allowed to route to.

The registry is the *capability surface* of the orchestration layer. The Supervisor's
planning prompt is built from it, plan validation is checked against it, and unknown
agent names are rejected before a plan ever reaches the dispatcher — so a
hallucinated specialist is a plan error the LLM is asked to fix, not a crash three
nodes later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Specialist
from .classification import ClassificationAgent
from .evidence import EvidenceAgent
from .policy import PolicyAgent
from .remediation import RemediationAgent


class AgentRegistry:
    """An ordered, name-keyed collection of specialists."""

    def __init__(
        self, agents: Optional[List[Specialist]] = None, data_dir: Optional[Path] = None
    ) -> None:
        self.data_dir = (
            Path(data_dir) if data_dir else Path(__file__).resolve().parent.parent / "data"
        )
        self._agents: Dict[str, Specialist] = {}
        for agent in agents or []:
            self.register(agent)

    def register(self, agent: Specialist) -> "AgentRegistry":
        if not agent.name:
            raise ValueError("agent has no name")
        if agent.name in self._agents:
            raise ValueError("duplicate agent name: {}".format(agent.name))
        self._agents[agent.name] = agent
        return self

    def get(self, name: str) -> Optional[Specialist]:
        return self._agents.get(name)

    @property
    def names(self) -> List[str]:
        return list(self._agents)

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: object) -> bool:
        return name in self._agents

    def describe(self) -> List[Dict[str, Any]]:
        """Catalogue rows — the same view feeds the planning prompt and the CLI."""
        return [self._agents[name].describe() for name in self._agents]


def default_registry(data_dir: Optional[Path] = None) -> AgentRegistry:
    """The four specialists this MVP ships: classify, apply policy, verify, remediate."""
    return AgentRegistry(
        agents=[ClassificationAgent(), PolicyAgent(), EvidenceAgent(), RemediationAgent()],
        data_dir=data_dir,
    )


__all__ = ["AgentRegistry", "default_registry"]
