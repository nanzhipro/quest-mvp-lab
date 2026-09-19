"""Evidence specialist — re-computes every reference the request claims to have.

It is the run's only authority on what counts as verifiable, and it produces the
``artifact index`` the rest of the pipeline cites against: rules are resolved
against the rule table, artifacts are located and their ``sha256`` recomputed from
bytes on disk. A claim whose digest does not match is *not* quietly dropped — it is
reported under ``unverified`` with a reason, which the consistency checker turns
into a repair and, if the repair cannot fix it, into an escalation. Verification
that cannot fail is not verification.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Mapping

from ..loader import data_file, load_json
from .base import AgentContext, Specialist


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceAgent(Specialist):
    """Verifies rule references and artifact digests, and publishes the citation index."""

    name = "evidence"
    mission = "证据核验（规则引用可解析、产物摘要可复算）"
    requires: tuple = ()
    required_output_keys = ("verified", "unverified", "index")

    def _rule_ids(self, data_dir: Path) -> set:
        document = load_json(data_file(data_dir, "policy_rules.json"))
        return {str(rule.get("id")) for rule in document.get("rules") or []}

    def _resolve_artifact(self, ctx: AgentContext, ref: str) -> Path:
        candidate = Path(ref)
        return candidate if candidate.is_absolute() else ctx.data_dir / "assets" / candidate

    def run(self, ctx: AgentContext) -> Dict[str, Any]:
        rule_ids = self._rule_ids(ctx.data_dir)
        items: List[Mapping[str, Any]] = [
            item for item in (ctx.request.get("evidence") or []) if isinstance(item, Mapping)
        ]
        verified: List[Dict[str, Any]] = []
        unverified: List[Dict[str, Any]] = []
        index: Dict[str, Dict[str, Any]] = {}
        for item in items:
            ref = str(item.get("id") or "").strip()
            kind = str(item.get("kind") or "").strip() or (
                "rule" if ref.startswith("rule:") else "artifact"
            )
            if not ref:
                unverified.append({"id": "", "kind": kind, "reason": "evidence item has no id"})
                continue
            if ref.startswith("rule:"):
                rule_id = ref.split(":", 1)[1]
                if rule_id in rule_ids:
                    verified.append({"id": ref, "kind": "rule", "note": "规则表可解析"})
                    index[ref] = {"kind": "rule", "ref": rule_id, "ok": True}
                else:
                    unverified.append({"id": ref, "kind": "rule", "reason": "规则表没有这条规则"})
                continue
            name = ref.split(":", 1)[1] if ref.startswith("artifact:") else ref
            path = self._resolve_artifact(ctx, name)
            if not path.is_file():
                unverified.append({"id": ref, "kind": "artifact", "reason": "产物不存在"})
                continue
            digest = sha256_of(path)
            declared = str(item.get("sha256") or "").strip()
            if declared and declared != digest:
                unverified.append(
                    {
                        "id": ref,
                        "kind": "artifact",
                        "reason": "摘要不匹配（声明 {}… 实算 {}…）".format(
                            declared[:12], digest[:12]
                        ),
                    }
                )
                continue
            size = path.stat().st_size
            verified.append(
                {"id": ref, "kind": "artifact", "sha256": digest, "size": size, "note": "摘要一致"}
            )
            index[ref] = {
                "kind": "artifact",
                "ref": path.name,
                "sha256": digest,
                "size": size,
                "ok": True,
            }
        return {
            "status": "ok",
            "verified": verified,
            "unverified": unverified,
            "index": index,
            "checked": len(items),
            "citations": [
                {
                    "id": item["id"],
                    "kind": "artifact" if item["id"] not in index else index[item["id"]]["kind"],
                }
                for item in verified
            ],
        }


__all__ = ["EvidenceAgent", "sha256_of"]
