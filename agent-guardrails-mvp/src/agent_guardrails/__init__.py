"""agent-guardrails-mvp —— 把 AI Agent 的安全边界放在模型之外的确定性代码里。

五层控制点（纵深防御，任何单层失效仍有兜底）：

| 层 | 类 | 作用 |
| --- | --- | --- |
| 输入 | :class:`~agent_guardrails.guardrails.InputGuard` | 长度上限 + 注入特征 + 机密检测（廉价规则先行）|
| 行动 | :class:`~agent_guardrails.gate.Gatekeeper` | **安全根基**：allowlist + 参数规则 + 风险分级 |
| 预算 | :class:`~agent_guardrails.budget.Budget` | 步数 / 工具调用 / token / 时长 / 同参数死循环 |
| 输出 | :class:`~agent_guardrails.guardrails.OutputGuard` | 机密与 PII 交付前脱敏 |
| 审计 | :class:`~agent_guardrails.audit.AuditLog` | 哈希链留痕，可离线复算证明未被改写 |

把护栏接到任意框架只有三个挂载点：工具调用前 ``gatekeeper.authorize(...)``，
执行后 ``gatekeeper.observe(...)``，每轮循环前 ``budget.tick_step(...)``。
"""

from .audit import AuditEntry, AuditLog, ChainVerification, verify_chain
from .budget import Budget, BudgetExceeded, BudgetLimits
from .config import Config, ConfigError, RunPaths
from .gate import (
    ApprovalDecision,
    ApprovalRequest,
    ConsoleApprover,
    DenyAllApprover,
    Gatekeeper,
    GateOutcome,
    ScriptedApprover,
    SessionContext,
    TaintState,
)
from .guardrails import (
    Decision,
    Hit,
    InputGuard,
    OutputGuard,
    Redaction,
    Risk,
    ScanResult,
    Verdict,
)
from .llm import ChatModel, Completion, DeepSeekClient, LLMError, ScriptedClient, ToolCall
from .runtime import GuardedAgent, RunResult, ToolTrace, summarise
from .scenarios import SCENARIOS, Check, Scenario, ScenarioRun, run_scenario
from .tools import ArgRule, ToolOutcome, ToolRegistry, ToolSpec, Workspace, workspace_tools

__version__ = "0.1.0"

# 按"控制点"分组而不是字典序：这份列表也是阅读这份导出的目录。
__all__ = [  # noqa: RUF022
    "__version__",
    # 审计
    "AuditEntry",
    "AuditLog",
    "ChainVerification",
    "verify_chain",
    # 预算
    "Budget",
    "BudgetExceeded",
    "BudgetLimits",
    # 配置
    "Config",
    "ConfigError",
    "RunPaths",
    # 闸门
    "ApprovalDecision",
    "ApprovalRequest",
    "ConsoleApprover",
    "DenyAllApprover",
    "Gatekeeper",
    "GateOutcome",
    "ScriptedApprover",
    "SessionContext",
    "TaintState",
    # 规则层
    "Decision",
    "Hit",
    "InputGuard",
    "OutputGuard",
    "Redaction",
    "Risk",
    "ScanResult",
    "Verdict",
    # 模型
    "ChatModel",
    "Completion",
    "DeepSeekClient",
    "LLMError",
    "ScriptedClient",
    "ToolCall",
    # 循环
    "GuardedAgent",
    "RunResult",
    "ToolTrace",
    "summarise",
    # 场景
    "SCENARIOS",
    "Check",
    "Scenario",
    "ScenarioRun",
    "run_scenario",
    # 工具
    "ArgRule",
    "ToolOutcome",
    "ToolRegistry",
    "ToolSpec",
    "Workspace",
    "workspace_tools",
]
