"""Specialist agents — the deterministic half of the orchestration layer.

Every agent here is a pure function of (request, dependency outputs, data files):
same input, same output, no model, no network, no clock. That is a design choice,
not a limitation — the MVP's whole claim is that a *routing* agent can be useful
while the domain answers stay reproducible and auditable. The Supervisor never
computes a level, a decision, or an action; it only decides who should.
"""

from __future__ import annotations

from .base import AgentContext, AgentError, Specialist
from .registry import AgentRegistry, default_registry

__all__ = [
    "AgentContext",
    "AgentError",
    "AgentRegistry",
    "Specialist",
    "default_registry",
]
