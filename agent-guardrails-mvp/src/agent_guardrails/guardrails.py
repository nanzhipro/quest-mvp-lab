"""输入/输出护栏：廉价规则先行的那两层（概率性兜底，不是安全根基）。

位置说明（为什么是兜底而不是边界）：正则只能拦住"已知形状"的攻击，
自适应攻击者可以绕过。它真正的价值是**在最低成本下收缩攻击面**，
并把可疑内容标记出来交给下游的确定性层（行动闸门 + 污点）处置。
安全根基永远在 :mod:`agent_guardrails.gate`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Sequence, Tuple


class Risk(Enum):
    """工具风险分级（参照 OpenAI Agent 指南的四因子评级：只读/可逆/权限/资金影响）。"""

    LOW = "low"  # 只读、可逆、无外部影响 → 放行
    MEDIUM = "medium"  # 可逆写入 / 内部影响   → 放行 + 限流 + 审计
    HIGH = "high"  # 不可逆 / 对外 / 涉金 → 必须人工审批


class Decision(Enum):
    """闸门结论。三态而不是布尔：``REQUIRE_APPROVAL`` 需要有人拍板才生效。"""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class Verdict:
    """一次判定的完整交代：结论 + 依据 + 命中的规则名（规则名让日志可统计）。"""

    decision: Decision
    reason: str
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class Pattern:
    """带名字的正则规则 —— 名字进日志/审计，正则本身可单测。"""

    def __init__(self, name: str, regex: str, describe: str) -> None:
        self.name = name
        self.regex = regex
        self.describe = describe
        self._compiled = re.compile(regex, re.IGNORECASE)

    def search(self, text: str) -> bool:
        return self._compiled.search(text) is not None


# 提示注入的已知形状（中英双语 + MCP 工具描述投毒的包裹标记）
INJECTION_PATTERNS: Sequence[Pattern] = (
    Pattern(
        "injection.ignore_previous",
        r"ignore\s+(all\s+)?(the\s+)?previous\s+instructions",
        "要求忽略既有指令",
    ),
    Pattern(
        "injection.ignore_previous_zh",
        r"忽略(之前|以上|先前|前面)的?(所有)?(指令|要求)",
        "要求忽略既有指令（中文）",
    ),
    Pattern("injection.role_override", r"you\s+are\s+now\b|从现在开始你是", "角色覆盖"),
    Pattern("injection.system_spoof", r"(^|\n)\s*system\s*[:：]\s*you", "伪造 system 角色"),
    Pattern("injection.important_tag", r"<\s*/?\s*(IMPORTANT|SYSTEM|ADMIN)\s*>", "MCP 工具描述投毒常见标记"),
    Pattern(
        "injection.reveal_prompt",
        r"reveal\s+(your|the)\s+(system\s+)?prompt|泄露(系统)?提示词",
        "套取系统提示词",
    ),
    Pattern(
        "injection.conceal",
        r"do\s+not\s+(mention|tell)[^\n]{0,40}(user|human)|不要告诉(用户|他人)",
        "要求对用户隐瞒",
    ),
    Pattern(
        "injection.exfiltrate",
        r"(send|forward|upload)[^\n]{0,40}(to|到)[^\n]{0,20}@"
        r"|(发送|转发|上传)[^\n]{0,20}(到|至)[^\n]{0,20}@",
        "可疑的外发指令",
    ),
)

# 机密材料形态：私钥块（整块，含正文）、API 令牌、云厂商 AK
SECRET_PATTERNS: Sequence[Pattern] = (
    # 私钥要按"整块"脱敏：只打码 BEGIN 那一行等于把密钥正文原样留在了输出里。
    Pattern(
        "secret.private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----([\s\S]{0,4000}?-----END [A-Z ]*PRIVATE KEY-----)?",
        "私钥块",
    ),
    Pattern("secret.api_token", r"\bsk-[A-Za-z0-9_-]{16,}", "API 令牌"),
    Pattern("secret.aws_key", r"\bAKIA[0-9A-Z]{16}\b", "AWS Access Key"),
)

# PII 形态（外发前的最后一道脱敏）
PII_PATTERNS: Sequence[Pattern] = (
    Pattern("pii.id_card", r"\b\d{17}[\dXx]\b", "身份证号"),
    Pattern("pii.phone", r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
    Pattern("pii.bank_card", r"(?<!\d)\d{16,19}(?!\d)", "银行卡号"),
)


@dataclass(frozen=True)
class Hit:
    """命中的规则（供日志解释"为什么"。）"""

    name: str
    describe: str

    def as_dict(self) -> Dict[str, str]:
        return {"rule": self.name, "why": self.describe}


@dataclass(frozen=True)
class ScanResult:
    """内容扫描结果：命中了哪些规则、要不要脱敏。"""

    hits: Tuple[Hit, ...] = ()
    reasons: Tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.hits

    def describe(self) -> str:
        return "，".join("{}（{}）".format(hit.name, hit.describe) for hit in self.hits)


class InputGuard:
    """入口规则层：长度上限 → 注入特征 → 机密材料。顺序按成本从低到高。"""

    def __init__(
        self,
        *,
        max_chars: int = 4000,
        injections: Sequence[Pattern] = INJECTION_PATTERNS,
        secrets: Sequence[Pattern] = SECRET_PATTERNS,
    ) -> None:
        self.max_chars = max_chars
        self._injections = tuple(injections)
        self._secrets = tuple(secrets)

    def check(self, text: str) -> Verdict:
        """对"用户输入"做准入判定：不合格直接拒绝（fail-closed）。"""
        if len(text) > self.max_chars:
            return Verdict(
                Decision.DENY,
                "输入超长：{} > {} 字符".format(len(text), self.max_chars),
                "input.too_long",
            )
        for pattern in self._injections:
            if pattern.search(text):
                return Verdict(
                    Decision.DENY,
                    "命中提示注入特征：{}（{}）".format(pattern.name, pattern.describe),
                    pattern.name,
                )
        for pattern in self._secrets:
            if pattern.search(text):
                return Verdict(
                    Decision.DENY,
                    "输入疑似包含机密材料：{}（{}）".format(pattern.name, pattern.describe),
                    pattern.name,
                )
        return Verdict(Decision.ALLOW, "通过：无命中规则", "input.clean")

    def scan(self, text: str, *, kinds: Sequence[Sequence[Pattern]] = ()) -> ScanResult:
        """对"不可信内容"做只读体检 —— 不拒绝（内容是数据），只留下证据供下游收紧。

        这是间接注入的第一道可见性：网页/邮件正文里藏着'忽略指令'时，
        这里会记录命中，随后工具本身的 ``produces_untrusted`` 会打上污点。
        """
        pools = list(kinds) if kinds else [self._injections]
        hits: List[Hit] = []
        reasons: List[str] = []
        for pool in pools:
            for pattern in pool:
                if pattern.search(text):
                    hits.append(Hit(pattern.name, pattern.describe))
                    reasons.append("命中 {}：{}".format(pattern.name, pattern.describe))
        return ScanResult(tuple(hits), tuple(reasons))


# 输出层要扫的全部规则：机密 + PII（模块级常量，便于测试直接引用与扩展）
REDACTION_PATTERNS: Sequence[Pattern] = (*SECRET_PATTERNS, *PII_PATTERNS)


@dataclass(frozen=True)
class Redaction:
    """输出脱敏结果：脱敏后的文本 + 命中的规则统计。"""

    text: str
    hits: Dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.hits)

    def describe(self) -> str:
        return "，".join("{}×{}".format(name, count) for name, count in sorted(self.hits.items()))


class OutputGuard:
    """出口规则层：把将要交付给用户/外部系统的文本里的机密与 PII 打码。"""

    PLACEHOLDER = "[已脱敏]"

    def __init__(self, patterns: Sequence[Pattern] = REDACTION_PATTERNS) -> None:
        self._patterns = tuple(patterns)
        self._compiled = tuple(
            (pattern, re.compile(pattern.regex, re.IGNORECASE)) for pattern in self._patterns
        )

    def sanitize(self, text: str) -> Redaction:
        hits: Dict[str, int] = {}
        for pattern, regex in self._compiled:
            text, count = regex.subn(self.PLACEHOLDER, text)
            if count:
                hits[pattern.name] = hits.get(pattern.name, 0) + count
        return Redaction(text, hits)

    def sanitize_payload(self, payload: Any) -> Tuple[Any, Dict[str, int]]:
        """对结构化结果（含工具输出）递归脱敏，供结果落盘前调用。"""
        hits: Dict[str, int] = {}
        if isinstance(payload, str):
            result = self.sanitize(payload)
            return result.text, dict(result.hits)
        if isinstance(payload, dict):
            out: Dict[Any, Any] = {}
            for key, value in payload.items():
                cleaned, sub_hits = self.sanitize_payload(value)
                out[key] = cleaned
                for name, count in sub_hits.items():
                    hits[name] = hits.get(name, 0) + count
            return out, hits
        if isinstance(payload, (list, tuple)):
            items = [self.sanitize_payload(item) for item in payload]
            for _, sub_hits in items:
                for name, count in sub_hits.items():
                    hits[name] = hits.get(name, 0) + count
            return [item[0] for item in items], hits
        return payload, hits
