"""The specialist contract: what an agent is given, and what it must return.

Three rules make the rest of the system simple:

1. **An agent never raises for a *content* problem.** A missing dependency or an
   unreadable asset is reported as ``status: "incomplete"`` plus ``missing_inputs``
   — the consistency checker then decides whether that is a repair or an escalation.
   Only genuine programming errors raise :class:`AgentError`.
2. **An agent declares what it needs.** ``requires`` names other agents' outputs;
   the dispatcher honours the plan's DAG and hands over whatever has already run.
   A disagreement between the plan and the contract is exactly the failure the
   consistency check is meant to catch.
3. **Every claim carries a citation.** ``citations`` hold ids resolvable against the
   rule table (``rule:R-…``) or an evidence artifact (``artifact:…``); aggregation
   and the consistency checker treat uncited claims as unverified claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


class AgentError(RuntimeError):
    """A programming error inside an agent (bad data file, malformed contract)."""


def mask(value: str, keep_prefix: int = 0, keep_suffix: int = 0) -> str:
    """Redact the middle of a match so evidence can be shown without leaking the value."""
    if len(value) <= keep_prefix + keep_suffix:
        return "*" * len(value)
    hidden = len(value) - keep_prefix - keep_suffix
    return "{}{}{}".format(value[:keep_prefix], "*" * hidden, value[len(value) - keep_suffix :])


@dataclass
class AgentContext:
    """Everything a specialist may look at for one sub-task execution."""

    request: Mapping[str, Any]
    goal: str
    data_dir: Path
    deps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dep_ids: Dict[str, str] = field(default_factory=dict)
    hint: str = ""
    attempt: int = 1

    def dep_output(self, agent: str) -> Dict[str, Any]:
        """The output of the dependency produced by ``agent``, or an empty dict."""
        return dict(self.deps.get(agent) or {})

    def asset_path(self) -> Path:
        """The file this request is about, resolved inside the packaged data dir."""
        asset = self.request.get("asset") or {}
        raw = str(asset.get("path") or "")
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else self.data_dir / "assets" / candidate

    def asset_name(self) -> str:
        asset = self.request.get("asset") or {}
        return Path(str(asset.get("path") or "")).name


class Specialist:
    """Base class for a deterministic specialist agent.

    Subclasses implement :meth:`run` and may override :meth:`missing_inputs` when the
    dependency is richer than "the other agent ran at all".
    """

    name: str = ""
    mission: str = ""
    requires: Tuple[str, ...] = ()
    required_output_keys: Tuple[str, ...] = ()

    # ── contract ──────────────────────────────────────────────────────────────
    def missing_inputs(self, ctx: AgentContext) -> List[str]:
        """Dependencies the plan failed to deliver before this agent ran."""
        return [agent for agent in self.requires if not ctx.dep_output(agent)]

    def run(self, ctx: AgentContext) -> Dict[str, Any]:
        raise NotImplementedError

    def invoke(self, ctx: AgentContext) -> Dict[str, Any]:
        """Run the agent and stamp the envelope every consumer relies on."""
        missing = self.missing_inputs(ctx)
        if missing:
            payload: Dict[str, Any] = {
                "status": "incomplete",
                "missing_inputs": missing,
                "citations": [],
                "reason": "{} needs {} before it can run".format(self.name, ", ".join(missing)),
            }
        else:
            payload = dict(self.run(ctx))
            payload.setdefault("status", "ok")
            payload.setdefault("missing_inputs", [])
            payload.setdefault("citations", [])
            # Field-level contract checks only mean something for a run that actually
            # happened; an agent that never ran reports the input it lacked instead.
            absent = [key for key in self.required_output_keys if key not in payload]
            if absent:
                payload["status"] = "incomplete"
                payload["missing_fields"] = absent
                payload["reason"] = "output is missing required field(s): {}".format(
                    ", ".join(absent)
                )
        payload["agent"] = self.name
        payload["attempt"] = ctx.attempt
        return payload

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "mission": self.mission,
            "requires": list(self.requires),
            "output_keys": list(self.required_output_keys),
        }
