"""审计日志：哈希链留痕（防篡改），以及可独立复算的校验器。

为什么需要哈希链：Agent 自报的轨迹与它的真实行为都可能被同一次注入伪造，
所以"每一次授权决策 + 依据 + 结果"必须由**编排器**（而不是模型）写下来，
并且要能被事后证明没有被改写。每条记录的摘要都包含前一条的摘要，
改动任意一行都会让后续所有摘要对不上 —— 用最小成本换来可验证的完整留痕。

审计与运行日志是两条流，各司其职：``obs`` 的回答是"发生了什么"，
``audit`` 的回答是"依据什么规则放行/拒绝了什么，且不可抵赖"。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

GENESIS = "0" * 16


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _digest(prev: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256("{}|{}".format(prev, canonical).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditEntry:
    """一条决策留痕。``digest`` 由 ``prev`` 与本条内容共同决定。"""

    seq: int
    ts: str
    event: str
    fields: Dict[str, Any] = field(default_factory=dict)
    prev: str = GENESIS
    digest: str = ""

    def as_line(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "fields": self.fields,
            "prev": self.prev,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ChainVerification:
    """校验结论：链是否完整、共几条、断在哪里。"""

    ok: bool
    entries: int
    reason: str = ""


class AuditLog:
    """追加写 + 哈希链。``path=None`` 时退化为内存链（测试与离线演示用）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else None
        self._entries: List[AuditEntry] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")  # 每次运行一条新链，不跨运行续接

    @property
    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    @property
    def head(self) -> str:
        return self._entries[-1].digest if self._entries else GENESIS

    def record(self, event: str, **fields: Any) -> AuditEntry:
        """记录一次决策（授权/审批/污点/熔断/执行结果）。"""
        seq = len(self._entries) + 1
        ts = _now()
        body = {"seq": seq, "ts": ts, "event": event, "fields": fields}
        entry = AuditEntry(
            seq=seq,
            ts=ts,
            event=event,
            fields=fields,
            prev=self.head,
            digest=_digest(self.head, body),
        )
        self._entries.append(entry)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.as_line(), ensure_ascii=False) + "\n")
        return entry


def verify_chain(path: Path) -> ChainVerification:
    """离线复算哈希链 —— 事后证明审计文件未被改写。"""
    entries = [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    prev = GENESIS
    for index, raw in enumerate(entries, start=1):
        if raw.get("prev") != prev:
            return ChainVerification(False, index - 1, "seq {} 的 prev 与上一条摘要不一致".format(index))
        body = {
            "seq": raw.get("seq"),
            "ts": raw.get("ts"),
            "event": raw.get("event"),
            "fields": raw.get("fields") or {},
        }
        if _digest(prev, body) != raw.get("digest"):
            return ChainVerification(False, index - 1, "seq {} 的内容摘要不匹配（记录被改动）".format(index))
        prev = raw["digest"]
    return ChainVerification(True, len(entries), "链完整，共 {} 条".format(len(entries)))


def iter_audit(path: Path) -> Iterator[Dict[str, Any]]:
    """按行读取审计文件（人读/工具读都走这里）。"""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)
