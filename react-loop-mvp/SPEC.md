# react-loop-mvp — SPEC（设计与契约）

> 本文是实现者视角的契约：数据长什么样、协议怎么解析、循环何时停、CLI 承诺什么、改动时必须同步什么。
> 背景与实测结论在 [`README.md`](README.md)；本文件不重复叙事，只写可被实现和测试直接引用的规则。

| 项 | 值 |
| --- | --- |
| 版本 | 0.1.0 |
| 读者 | 实现者 / 复现者 / 想把手写循环搬到自己项目里的人 |
| 状态 | 已实现，187 个离线测试 + 真实 DeepSeek 端到端验证通过（证据见 `runs/20260916_173649_live/`） |

## 1. 目标与非目标

**目标**

1. 用最少的代码把 ReAct 循环（Thought → Action → Action Input → Observation）跑通，且**每一行都能被读懂**。
2. 让「框架替你做的事」显式化：写协议、解析回复——其余部分与普通代码无异。
3. 同一套工具用两种协议（自写文本协议 / provider 原生 function calling）实现，可同题对照。
4. 全程可离线验证：测试不需要 API key，不依赖网络，不依赖时钟。

**非目标**

- 不做 RAG 方案（检索工具只是让轨迹可复现的道具）。
- 不做流式输出、多轮会话记忆、并发、多用户。
- 不追求参数类型系统（文本协议只有一个字符串参数；native 模式也只是把同一参数包一层 schema）。
- 不引入任何第三方运行时依赖（这是硬约束，见 §2）。

## 2. 硬约束（由 `tests/test_project_rules.py` 强制）

| # | 约束 | 强制方式 |
| - | ---- | -------- |
| C1 | 运行时依赖为空 | 断言 `pyproject.toml` 含 `dependencies = []` |
| C2 | 只用标准库 | AST 扫描 `src/**` 的 import，与 `sys.stdlib_module_names` 求差 |
| C3 | 不出现 agent 框架 / 第三方 HTTP 客户端 | 正则扫描 import 黑名单（langchain · llamaindex · langgraph · autogen · crewai · dspy · smolagents · pydantic_ai · haystack · openai · anthropic · httpx · requests） |
| C4 | 核心循环保持短小 | 数 `react.py` 的 `core loop` 标记之间非空行 ≤ 30，且必须含 `model.complete` / `parse_reply` / `registry.call_text` / `append_observation` 四个动作 |
| C5 | 核心模块不依赖 argparse / 打印 | `react.py` `native.py` `tools.py` `parsing.py` 中禁止 `import argparse`、`print(`、`sys.stdout` |
| C6 | 无企业数据资产 | 扫描品牌字样、公司 bundle id、签名标识（含 README / SPEC 自身） |
| C7 | 无证书 / 描述文件入库 | 按扩展名断言 |
| C8 | README 摘录的循环与源码逐字一致 | 从 `react.py` 抽取循环文本，断言其出现在 `README.md` 中 |
| C9 | 每个模块自带说明 | 断言模块 docstring 长度 > 80 字符 |

## 3. 组件与数据流

```
                        ┌──────────────────────────── cli.py ────────────────────────────┐
                        │ 参数路由 · 密钥解析 · 产物落盘 · 退出码 0/1/2                    │
                        └───────┬───────────────────────────────┬───────────────────────┘
                                │ 构造 model                     │ 构造 tracer（控制台 + JSONL）
                                ▼                                ▼
   ┌── DeepSeekClient ──┐  ChatModel(Protocol)         ┌──────────── 循环 ────────────┐
   │ urllib POST        │◄─── 2 方法：complete /        │ react.py   ← 手写文本协议      │
   │ 重试 · 退避 · 留痕  │     exchanges                │ native.py  ← function calling │
   └─────────┬──────────┘                               └───────┬───────────────┬───────┘
             │                                                  │               │
             ▼                                          ToolRegistry            │
     api.deepseek.com                          （calculator / search_docs）      │
             │                                                  │               │
             └──────────── ScriptedClient（离线重放） ───────────┘               │
                                                                                ▼
                                                        trace.jsonl · wire.jsonl · result.json
```

依赖方向单向：`cli → {react, native} → {llm, tools, parsing, trace} → config`。核心模块不知道 CLI 存在。

## 4. 数据契约

### 4.1 内存对象（`llm.py` / `tools.py` / `react.py` / `native.py`）

| 类型 | 字段 | 语义 |
| ---- | ---- | ---- |
| `ToolCall` | `name`, `arguments: dict`, `arguments_raw: str`, `call_id: str` | provider 返回的一次工具调用；`arguments_raw` 保留原文，`arguments` 解析失败时退化为 `{}` |
| `Completion` | `text`, `tool_calls[]`, `reasoning`, `usage: dict`, `latency_ms`, `finish_reason` | 一次模型回复，跨 provider 归一 |
| `ToolSpec` | `name`, `summary`, `param_name`, `param_description`, `handler: str → str` | 工具的公开面；`catalog_line()` 供文本协议，`json_schema()` 供 native 协议 |
| `ToolResult` | `tool`, `argument`, `output`, `ok` | 一次工具执行；失败也是结果（`ok=False` 且 `output` 是可读原因） |
| `ReActStep` | `index`, `thought`, `action`, `action_input`, `observation`, `ok`, `latency_ms`, `usage`, `prompt_chars`, `dropped_hallucinated_observation` | 文本协议的一步；`prompt_chars` 记录进入本步时 scratchpad 的字符数 |
| `ReActResult` | `task`, `answer`, `steps[]`, `stopped_reason`, `elapsed_ms`, `usage_totals`, `last_text`, `last_step_prompt_chars` | 一次 ReAct 运行的终态 |
| `ToolCallRecord` / `NativeStep` / `NativeResult` | 同上，按消息数组组织；`NativeResult.messages` 保留完整消息序列 | native 协议的一步与终态 |

`answer is None` 表示「没有拿到 Final Answer」，此时 `stopped_reason` 必为 `max_steps` / `loop_detected` / `unparsable` 之一；
**不允许**用兜底文本冒充答案（README 与 CLI 会把 `last_text` 原样展示为「最后一轮回复」）。

### 4.2 磁盘契约（`--trace-dir DIR` 产出三件）

| 文件 | 内容 | 关键字段 |
| ---- | ---- | -------- |
| `trace.jsonl` | 每次运行的事件流，追加写入（崩溃也留痕） | 公共 `ts` / `event` / `run_id`；`task`；`prompt`（每步完整 prompt 原文）+ `step`；`note`；`answer`；`stop` |
| `wire.jsonl` | 每一次 HTTP 交换的原始字节 | `run_id`, `index`, `attempt`, `status`, `latency_ms`, `error`, `request`（method/path/body）, `response_raw`, `endpoint` |
| `result.json` | `ReActResult.as_dict()` 或 `NativeResult.as_dict()` 的缩进 JSON | `mode`, `task`, `answer`, `stopped_reason`, `steps[]`, `tool_calls`, `elapsed_ms`, `usage_totals` |

约束：`wire.jsonl` / `trace.jsonl` 中**不得出现完整密钥**（`assert "sk-test-key" not in json.dumps(exchange)`，测试强制）；
`usage` 一律取自 provider 响应，不估算、不编造（离线演示脚本刻意不写 `usage`）。

### 4.3 运行标识

`run_id = <YYYYmmdd_HHMMSS>_<mode>`；一次运行一个时间戳目录（`runs/<ts>_<用途>/<react|native>/`），
`trace.jsonl` 追加语义意味着复用目录会把两次运行混在一起——要干净证据就给新目录。
`runs/` 不入库（含真实 API 响应），因此本文与 README 引用的证据路径是本地路径；复现命令见 README。

## 5. 协议契约（文本协议）

### 5.1 语法

```
reply        := (thought_line)* ( action_block | final_block ) (observation_block)? 
thought_line := ["Thought" | "思考" | "推理"] ":" text
action_block := "Action:" tool_name [ "(" arg ")" | "[" arg "]" ] NEWLINE ["Action Input:" arg]
final_block  := ["Final Answer" | "最终答案" | "答案"] ":" text+
arg          := 单行字符串（允许被引号 / 反引号 / 星号包裹）
```

解析规则（`parsing.parse_reply`，返回 `ParseOutcome.kind ∈ {action, final_answer, unparsed}`）：

| # | 规则 | 理由 |
| - | ---- | ---- |
| P1 | 大小写不敏感，接受 `Action：` 全角冒号、`**Action:**` 装饰、`` `name` `` 反引号包裹 | 真实模型会装饰；解析器宽容，规则不变 |
| P2 | `Final Answer` 优先级高于 `Action` | 已经给出答案的一轮不应再执行动作 |
| P3 | `Final Answer` 的多行内容在下一个协议行（Action/Action Input/Final Answer）处截断 | 防止把后续协议文本当答案 |
| P4 | `Action` 缺 `Action Input` 仍解析成功（`argument=None`） | 交给注册表报「缺 Action Input」，模型据此自我纠正，而不是崩循环 |
| P5 | `Action Input` 只认紧随其后的第一条（跳过空行与无关行，遇下一个协议行即停） | 避免把第二个动作的入参粘到第一个动作上 |
| P6 | 首个 `Observation:` 行之后的内容全部丢弃并置位 `hallucinated_observation=True` | 模型自造的观察一旦回填就是自欺；截断比反复叮嘱更可靠 |
| P7 | `thought` 去掉模型自带的 `Thought:` 标签（渲染器会补） | 避免 `Thought: Thought: ...` |
| P8 | 入参清洗：去首尾引号 / 反引号 / 星号，全角引号按对剥离 | 模型习惯性加壳 |

未知工具、缺参数、工具抛异常三种情况**不抛异常**，统一转成 `ToolResult(ok=False, output=可读原因)` → Observation。

### 5.2 两种协议的等价映射

| ReAct 文本 | native function calling |
| ---------- | ----------------------- |
| `TASK_TEMPLATE` 里的 `- calculator[expression]: …` 目录 | `tools=[ToolSpec.json_schema()]` |
| `Action: calculator` + `Action Input: (3+4)*5` | `tool_calls[0].function = {"name": "calculator", "arguments": "{\"expression\": \"(3+4)*5\"}"}` |
| `Observation: 35`（追加进 scratchpad） | `{"role": "tool", "tool_call_id": …, "name": …, "content": "35"}` |
| 一条不断变长的 `user` 消息 | `messages` 数组（assistant 的 `tool_calls` 必须原样回填） |
| 解析失败保护 | 不需要（格式由 provider 保证） |

两种模式共用同一 `ToolRegistry` 实例的同一 handler，因此「工具行为」不是对照变量。

## 6. 循环契约

### 6.1 文本协议循环（`ReActAgent.run`）

| 条件 | 行为 | `stopped_reason` |
| ---- | ---- | ---------------- |
| 回复含 `Final Answer` | 立即返回该答案 | `final_answer`（`succeeded=True`） |
| 回复既非 Action 也非 Final Answer | 记一步（`ok=False`），Observation 为纠正提示，scratchpad 追加 `PARSE_FAILURE_NUDGE` 后继续 | 连续 3 次后 → `unparsable` |
| 有 Action | 调工具 → 记一步 → `append_observation`（末尾补 `Thought:` 提示继续） | — |
| 同一 `(工具名, 入参)` 连续第 4 次出现 | 在执行前停下（工具不会被执行） | `loop_detected` |
| 达到 `max_steps`（默认 8） | 结束 | `max_steps` |

`parse_failure_limit` / `repeat_limit` 默认均为 3，可在构造函数覆盖（测试用到）。

### 6.2 native 循环（`NativeAgent.run`）

| 条件 | 行为 | `stopped_reason` |
| ---- | ---- | ---------------- |
| 回复无 `tool_calls` | 其 `content` 即最终答案 | `final_answer` |
| 有 `tool_calls` | 逐个执行（支持同轮多个），assistant 消息原样回填 + 逐个 tool 消息 | — |
| 同轮工具调用签名连续重复 3 次 | 停下 | `loop_detected` |
| 达到 `max_steps` | 结束 | `max_steps` |

### 6.3 预算与成本

- `max_steps` 限制的是**模型轮数**，不是工具调用数；native 一轮可含多个工具调用。
- `temperature` 默认 0.0、无随机源（除重试退避抖动），同一脚本化模型可完全复现。
- `usage_totals` = 各步 `usage` 与终止步 `usage` 的逐键求和。

## 7. CLI 契约

| 命令 | 参数 | 行为 |
| ---- | ---- | ---- |
| `react-loop [TASK...]` | 位置参数拼成问题；缺省时读 stdin（非 TTY）；两者皆无 → 用法错误退出 2 | 默认跑 ReAct |
| `--mode {react,native}` | 默认 `react` | 选协议 |
| `--model` / `--base-url` | 覆盖环境变量（不提供默认密钥） | 端点切换 |
| `--max-steps N` | 默认 8 | 轮数预算 |
| `--temperature` / `--max-tokens` | 默认 0.0 / 不发送 | 采样控制 |
| `--fake` / `--script FILE` | 用内置 / 自定义脚本化模型，免 key | 离线重放 |
| `--trace-dir DIR` | 落盘三件产物，路径打到 stderr | 证据 |
| `--show-prompt` / `--show-reasoning` | 打印每步完整 prompt / 推理字段 | 教学 |
| `--json` | stdout 只输出结构化结果 | 机器消费 |
| `probe` | 模型清单 + 一次 ping + 用量；`/models` 不可用时降级为提示而非失败 | 连通性自检 |
| `version` / `--version` | 版本号 | — |

**退出码**：`0` 成功（含 `--fake`）；`1` 运行失败（模型错误、未在预算内给出 Final Answer、probe 的 ping 失败）；
`2` 配置错误（缺 key、脚本文件不存在、脚本缺对应 mode 段、缺任务）。

**stdout/stderr 分工**：答案（或 `--json` 的结构体）走 stdout；过程 trace、`wrote <path>`、错误提示走 stderr。
`NO_COLOR` 与「非 TTY」都会关闭颜色。

## 8. 网络与错误契约（`DeepSeekClient`）

| 情形 | 处理 |
| ---- | ---- |
| 408 / 409 / 425 / 429 / 500 / 502 / 503 / 504 | 重试，最多 `max_retries`（默认 3）次 |
| 其它 4xx（401 / 403 / 400） | **不重试**，立即 `LLMError`（重试只会烧预算） |
| `URLError` / `socket.timeout` / `TimeoutError` / `ConnectionError` | 重试 |
| 响应体非 JSON | `LLMError`（并留痕原文前 300 字符） |
| 响应缺 `choices` | `LLMError` |
| 退避 | `min(8s, 0.5 × 2^(n-1))` + 抖动 ≤ 0.25s，可注入 `sleep` 以便测试 |
| 超时 | `timeout_s` 默认 60s，作用于单次请求 |

请求体：`{model, messages, temperature, stream: false}`，有工具时追加 `tools` 与 `tool_choice: "auto"`；`max_tokens` 仅在配置时出现。
认证：`Authorization: Bearer <key>`，密钥只从 `DEEPSEEK_API_KEY` / `--api-key` 之外的显式参数读取，
且 `Config.api_key` 的 `repr=False`、`masked_key()` 只暴露尾 4 位。

## 9. 可测性契约

- **`ChatModel` Protocol**（`complete(messages, tools=None) → Completion`，`exchanges` 属性）是循环唯一知道的模型接口。
- **`ScriptedClient`** 逐次重放脚本条目；条目可以是字符串或 `{content, tool_calls, reasoning, usage, finish_reason}`；
  脚本耗尽时抛 `LLMError`（**绝不编造回复**），并暴露 `prompts` / `calls` / `exchanges` 供断言。
- **离线演示**（`data/demo_script.json`）是同一机制的产品化用法：`--fake` 走它，不需要 key。
  它的每个检索 query 都必须能在内置语料里命中（测试强制），且**不得携带 `usage`**（避免假账单）。
- 测试不得读环境变量里的真实密钥、不发真实 HTTP（`urlopen` 被 monkeypatch）、不依赖真实时钟。

测试矩阵（187 项）按模块：`config` 9 · `parsing` 16 · `tools` 50 · `llm` 22 · `react` 21 · `native` 12 ·
`trace` 17 · `cli` 22 · `offline_demo` 7 · `project_rules` 11（以 `pytest --collect-only` 为准）。

## 10. 决策记录（ADR）

| # | 决策 | 备选 | 取舍 |
| - | ---- | ---- | ---- |
| D1 | 轨迹放一条变长的 user 消息 | 消息数组（更接近 chat 习惯） | ReAct 原教旨、最直观展示「上下文就是字符串」；代价是无法一轮多工具 |
| D2 | 每个工具一个字符串参数 | 每工具自定义多参数 | 保证两种协议表达同一件事；代价是参数类型信息缺失 |
| D3 | 工具异常转 Observation | 直接抛错终止 | 让模型有机会自我纠正；代价是可能多烧一轮 |
| D4 | 幻觉 Observation 截断 | 提示词强调「不要编造」 | 截断是确定性防线；提示词只是概率防线。两者都保留 |
| D5 | 两处循环保护写进循环本体 | 抽成守卫对象 | 保护只有 9 行，写在这里读者能看到成本上限从哪来 |
| D6 | 离线演示随包发布 | 只提供真跑路径 | 让评审/分享场景零门槛复现；代价是需维护脚本与语料的一致性（有测试） |
| D7 | trace 追加写而非覆盖 | 覆盖写 | 崩溃留痕优先；靠「一次运行一个目录」的约定避免混淆 |
| D8 | `requires-python >= 3.9` | 3.11+ 用新语法 | 同族 MVP 与用户机器一致；代价是关掉 ruff `UP`、手写 `typing.List/Optional` |
| D9 | 中文提示词与中文观察 | 英文 | 与使用者的提问语言一致，减少一次隐式翻译；解析器同时接受中英关键字 |

## 11. 变更规程

| 改动 | 必须同步 |
| ---- | -------- |
| 改 `react.py` 的循环本体 | README 的代码块（用 §12 的脚本重新注入）+ `test_readme_quotes_the_actual_loop` |
| 改解析规则 | `tests/test_parsing.py` 的用例 + SPEC §5.1 规则表 + PROMPT 模板里的格式说明 |
| 加/改工具 | `default_registry()` + `tests/test_tools.py` + SPEC §5.2 映射表 + README 能力表 |
| 改 `result.json` / `trace.jsonl` 字段 | `tests/test_cli.py::test_json_keys_are_stable` + SPEC §4.2 + README 的实测数字来源说明 |
| 改 `demo_script.json` / `kb.json` | `tests/test_offline_demo.py`（两跳可命中）+ `tests/test_tools.py` 的语料断言 |
| 任何新增 | 不得引入第三方运行时依赖（C1/C2 会红灯） |

重新注入 README 代码块：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
src = Path("src/react_loop_mvp/react.py").read_text(encoding="utf-8")
start = src.index("core loop (start)"); start = src.index("\n", start) + 1
end = src.rindex("\n", 0, src.index("core loop (end)")) + 1
loop = src[start:end].rstrip("\n")
readme = Path("README.md").read_text(encoding="utf-8")
a = readme.index("```python\n") + len("```python\n")
b = readme.index("\n```", a)
Path("README.md").write_text(readme[:a] + loop + readme[b:], encoding="utf-8")
PY
```

## 12. 验收清单

| # | 验收项 | 证据 |
| - | ------ | ---- |
| A1 | 187 个测试全绿，全程离线 | `pytest` 输出 |
| A2 | lint 与格式零告警 | `ruff check .` / `ruff format --check .` |
| A3 | 无 key 可完整重放一条多跳轨迹 | `react-loop --fake`（两种 mode 各一次） |
| A4 | 真机两跳问答正确 | `runs/20260916_173649_live/react/result.json` |
| A5 | 真机混合工具（计算 + 检索）正确 | `runs/20260916_173649_live/react_mixed/result.json` |
| A6 | 原生 function calling 同题正确 | `runs/20260916_173649_live/native/result.json` |
| A7 | 原始字节与用量可复核 | 各目录 `wire.jsonl`（含 `usage`、`latency_ms`） |
| A8 | 无框架依赖 | `tests/test_project_rules.py::test_no_agent_framework_is_imported` |
| A9 | 无企业数据 / 证书入库 | `tests/test_project_rules.py::test_no_enterprise_asset_*` |
| A10 | 文档与源码一致 | `test_readme_quotes_the_actual_loop` |
