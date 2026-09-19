"""supervisor-graph-mvp — a two-piece orchestration layer you can read in one sitting.

The design has exactly two moving parts, and this package keeps them separate on purpose:

``Supervisor`` (``supervisor.py``)
    A lightweight-model agent with four narrow duties and nothing else: **intent
    recognition**, **task planning** (a DAG of sub-tasks), **routing** (hand each
    sub-task to the specialist that owns it) and **aggregation** (merge the
    specialists' outputs and check them for consistency). It never answers a
    domain question itself.
``StateGraph`` (``graph.py``)
    The execution spine: named nodes, static and conditional edges, one shared
    state with per-key reducers, a step budget and a full visit log. Cycles are
    legal — the repair loop (``check → dispatch``) is one.

Specialist agents are deterministic (``agents/``) so that the only non-deterministic
surface in the whole system is the Supervisor's three narrow model calls.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .graph import END, CompiledGraph, StateGraph
from .pipeline import Orchestrator

__all__ = ["END", "CompiledGraph", "Orchestrator", "StateGraph", "__version__"]
