"""react-loop-mvp — a ReAct loop small enough to read in one sitting.

The package is deliberately layered so that the *pedagogical* part (the loop)
sits on top of boring, testable infrastructure:

``config``   where the key / endpoint / model come from (environment only)
``llm``      one HTTP verb (``POST /chat/completions``), one retry policy
``tools``    the tool registry shared by both protocols
``parsing``  the text protocol: ``Action:`` / ``Action Input:`` / ``Final Answer:``
``react``    the ~20-line hand-written loop  ← the point of the MVP
``native``   the same loop expressed with native function calling (for contrast)
``trace``    per-step JSONL + human-readable console trace
``cli``      argument routing and wiring
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
