# react-loop-mvp — 手写一个 ReAct 循环，拆到看得懂

> **A hand-written ReAct loop you can read in one sitting — 28 lines of loop, zero frameworks, zero runtime
> dependencies, talking to DeepSeek through `urllib`. The same task is then re-implemented with native
> function calling so the two can be compared line by line.**
>
> 先用手写循环理解本质，再上框架——这样框架的抽象是「看得懂的便利」，而不是黑盒魔法。

![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Deps: stdlib only](https://img.shields.io/badge/deps-stdlib%20only-brightgreen.svg)
![Framework: none](https://img.shields.io/badge/framework-none-critical.svg)
![Model: deepseek-chat](https://img.shields.io/badge/model-deepseek--chat-orange.svg)
![Tests: 187 offline](https://img.shields.io/badge/tests-187%20offline-success.svg)
![Live: verified](https://img.shields.io/badge/live%20E2E-verified-success.svg)

## Features

- **核心循环 28 行**：`Thought → Action → Action Input → Observation` 的完整 ReAct 循环，含两处保护，全部在
  [`src/react_loop_mvp/react.py`](src/react_loop_mvp/react.py) 的 `core loop` 标记之间，一屏读完。
- **零依赖、零框架**：运行时依赖为空（`dependencies = []`），网络只用标准库 `urllib`；没有 LangChain、
  没有 LlamaIndex、没有官方 SDK。测试里有强制门禁（`tests/test_project_rules.py`）扫描 import，违规则红灯。
- **两种协议同题对照**：`--mode react`（自己写协议、自己解析）与 `--mode native`（provider 的 `tool_calls`）
  共用同一套工具、同一份预算、同一个 tracer，唯一变量是「协议由谁负责」。
- **离线可跑**：`react-loop --fake` 用内置脚本化模型重放一条真实的多跳轨迹，不需要 API key、不发一个请求。
- **证据留痕**：`--trace-dir DIR` 落盘 `trace.jsonl`（每一步的完整 prompt、模型原始回复、观察）、
  `wire.jsonl`（每一次 HTTP 请求/响应原文）、`result.json`（结构化结果）。
- **可注入的模型客户端**：`ChatModel` 是一个两方法的 Protocol；`DeepSeekClient` 走真网，`ScriptedClient`
  确定性地重放脚本。循环因此可以在测试里被完全断言，不需要 mock 框架。
- **真实错误处理**：408/429/5xx 与网络错误指数退避重试，4xx 立即失败；工具异常不会炸掉循环，而是变成一条
  Observation 交给模型；模型自己编的 `Observation:` 会被截断，防止它把幻觉喂回上下文。

## 背景

框架的价值在于替你做了两件事：**写协议**（提示词里的工具目录、JSON schema）和**解析回复**（判断模型是否在调工具、
参数是什么）。这两件事不做任何推理，却是所有 agent 框架最厚的一层封装。

所以入门路径应该是反过来的：先用纯 API 手写一遍，看清这两个动作的真面目，再决定要不要买框架的便利。本项目就是
那个「手写一遍」的产物：

1. 一次真实的 ReAct 轨迹，落在**一条不断变长的 user 消息**里（不是消息数组）——这是 ReAct 论文的原始形态。
2. 工具执行结果只以 `Observation:` 一行的形式回填，模型看到的世界就是这行字符串。
3. 把同样的工具、同样的循环用原生 function calling 再写一遍，量化两者差在哪（见 [实测结果](#实测结果)）。

## 核心循环

以下是 `src/react_loop_mvp/react.py` 中 `core loop` 标记之间的真实代码（本文件的代码块由脚本从源码注入，
`tests/test_project_rules.py::test_readme_quotes_the_actual_loop` 会校验两者一致）：

```python
    def run(self, task: str) -> ReActResult:
        """Think → act → observe until a Final Answer, a budget stop, or a guard."""
        started = time.monotonic()
        prompt = build_prompt(task, self.registry.catalog())
        steps: List[ReActStep] = []
        previous, repeats = None, 0
        for index in range(1, self.max_steps + 1):
            self.tracer.record("prompt", step=index, prompt=prompt)
            reply = self.model.complete(to_messages(prompt))
            outcome = parse_reply(reply.text)
            if outcome.final_answer is not None:
                return self._result(
                    task, steps, "final_answer", started, reply, outcome.final_answer
                )
            if outcome.action is None:
                if self._reject(index, outcome, steps, reply, prompt) >= self.parse_failure_limit:
                    return self._result(task, steps, "unparsable", started, reply)
                prompt = append_observation(prompt, outcome.text, PARSE_FAILURE_NUDGE)
                continue
            key = (outcome.action.name, outcome.action.argument)
            repeats = repeats + 1 if key == previous else 0
            previous = key
            if repeats >= self.repeat_limit:
                return self._result(task, steps, "loop_detected", started, reply)
            result = self.registry.call_text(*key)
            steps.append(self._record(index, outcome, result, reply, prompt))
            prompt = append_observation(prompt, outcome.text, result.output)
        return self._result(task, steps, "max_steps", started)
```

去掉 1 行 docstring 与 2 处保护（解析失败退出、同一动作重复三次退出），真正属于 ReAct 的动作只有 17 行：

1. `build_prompt()` 把工具目录和问题拼成一根字符串；
2. `model.complete(to_messages(prompt))` —— 想；
3. `parse_reply()` 用一个正则读协议 —— 拆；
4. `registry.call_text()` 执行工具 —— 做；
5. `append_observation()` 把 `Observation` 追加回那根字符串，并以 `Thought:` 结尾提示模型继续 —— 记。

框架里对应的是 AgentExecutor、tool node、message reducer、graph state；在这里它们就是 5 行函数调用。

## 两种协议：一个动作，两种写法

| 维度 | `--mode react`（文本协议） | `--mode native`（function calling） |
| ---- | -------------------------- | ---------------------------------- |
| 谁写工具协议 | 本项目：`ToolSpec.catalog_line()` 拼提示词 | provider：`ToolSpec.json_schema()` 进 `tools=` |
| 谁解析回复 | 本项目：`parsing.parse_reply()`（正则 + 容错） | provider：`choices[0].message.tool_calls` |
| 轨迹载体 | **一条 user 消息**，逐轮变长 | `messages` 数组：system / user / assistant / tool |
| 一轮多个工具 | 不支持（一轮一个 Action，这是 ReAct 的原教旨） | 支持并行 `tool_calls` |
| 参数类型 | 只有一个字符串参数 | 仍是同一个字符串参数，但被 JSON schema 包了一层 |
| 循环长度 | 28 行 | 58 行（多出来的全在消息装配与回填） |
| 循环保护 | 解析失败 / 同一动作重复 | 同一动作重复（格式由 provider 保证） |

两者共用 `ToolRegistry` 的**同一个 handler**，所以「工具行为不同」不是变量——唯一变量是协议的归属。

## 快速开始

```bash
# 1. 装（只需要 pytest 与 ruff；运行时零依赖）
uv venv --python 3.13 .venv
VIRTUAL_ENV=.venv uv pip install -e ".[dev]"        # 或 python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 2. 离线跑一遍，不需要 API key：脚本化模型重放两跳检索轨迹
.venv/bin/react-loop --fake
.venv/bin/react-loop --fake --mode native           # 同一轨迹的原生 function calling 版本
.venv/bin/react-loop --fake --show-prompt            # 想看每一步发出去的完整上下文

# 3. 真跑（密钥只走环境变量，从不进仓库）
export DEEPSEEK_API_KEY=sk-...
.venv/bin/react-loop probe                           # 连通性 + 模型清单自检
.venv/bin/react-loop "先用 calculator 算 (23*17+11)/4，再查文档库「工具调用沙箱链」属于哪个项目。"
.venv/bin/react-loop --trace-dir runs/demo "把每一步的原始字节留下"    # trace + wire + result 三件产物
```

环境变量：`DEEPSEEK_API_KEY`（必需）、`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）、
`DEEPSEEK_MODEL`（默认 `deepseek-chat`）；命令行 `--model` / `--base-url` 会覆盖环境变量，但**不会**凭空造一个默认密钥。

### 命令速查

| 命令 | 作用 |
| ---- | ---- |
| `react-loop "问题"` | 默认动作：ReAct 文本协议跑一次（问题也可从 stdin 管道进来） |
| `react-loop --mode native "问题"` | 原生 function calling 对照 |
| `react-loop --fake` / `--script FILE.json` | 离线重放内置 / 自定义脚本 |
| `react-loop --show-prompt` / `--show-reasoning` | 打印每步完整 prompt / 模型的推理字段 |
| `react-loop --trace-dir DIR` | 落盘 `trace.jsonl` + `wire.jsonl` + `result.json` |
| `react-loop --json "问题"` | stdout 只输出结构化结果（供其它程序解析） |
| `react-loop --max-steps N` | 轮数预算（默认 8） |
| `react-loop probe` | 连通性自检：模型清单 + 一次 ping + 用量 |
| `react-loop version` | 版本号 |

## 实测结果

对真实 `https://api.deepseek.com`（`deepseek-chat`，`temperature=0`）跑三组轨迹，产物在
[`runs/20260916_173649_live/`](runs/20260916_173649_live/)（`react/`、`react_mixed/`、`native/`，每个目录
三件产物齐备）。以下数字全部取自 `result.json` / `wire.jsonl` 的 `usage` 与 `latency_ms`，未经润色。

**同一道题，两种协议：**

| 指标 | `--mode react` | `--mode native` |
| ---- | -------------- | --------------- |
| HTTP 调用 | 2 | 2 |
| 工具调用 | 1（`search_docs`） | 2（**同一轮并行**两次 `search_docs`） |
| `prompt_tokens` 合计 | **927**（236 + 691） | **1,828**（470 + 1,358） |
| `completion_tokens` | 232 | 333 |
| 端到端 | 2,189 ms | 2,628 ms |
| 轨迹长度 | 378 → 691 字符（同一条 user 消息） | messages 数组 4 条消息 |
| 答案 | 316 字，一段话给全 | 488 字，带小标题 |
| 结果正确性 | ✅ 项目 `es-mvp`、规范 `es-mvp/SPEC.md` §9、三条结论 | ✅ 同上 |

同题同对错的前提下，手写文本协议的输入 token 只有原生方案的 **51%**：原生方案每一轮都要带上 JSON 工具 schema，
并把 assistant/tool 成对消息重新发一遍；文本方案的工具目录只出现在那一根不断变长的字符串里，且前缀天然命中缓存。

**混合工具轨迹（`react_mixed/`）：**

| 步骤 | 动作 | 观察 |
| ---- | ---- | ---- |
| 1 | `calculator` ← `(23*17+11)/4` | `100.5` |
| 2 | `search_docs` ← `工具调用沙箱链` | 命中 `sandbox-center-mvp`（804 字符） |
| 3 | —（Final Answer） | `算式 (23*17+11)/4 的结果是 100.5，而「工具调用沙箱链」属于 sandbox-center-mvp 项目。` |

3 次 HTTP 调用、1,254 + 118 token、2,006 ms，scratchpad 从 388 增长到 495 字符——`prompt_chars` 字段记录的就是
这个增长过程。

**两跳题在实测里被合并成一跳**：真实模型把查询写成 `quest-mvp-lab 目录级 AUTH_OPEN 规范文档`，top-3 检索同时
返回了 `es-mvp` 与 `es-mvp-spec-9`，于是它一轮就收敛了。内置的离线脚本保留了两跳轨迹（第一跳只返回项目、第二跳
才拿到规范结论），因为那才是 ReAct 想教的东西：**下一跳的查询由上一跳的观察决定**。

## 可视化

[`diagrams/full-chain.html`](diagrams/full-chain.html) —— 全链路时序图（自包含单文件 HTML，浏览器直接打开）：

- **一条链路画全**：`ReAct 循环 → DeepSeekClient → api.deepseek.com → ToolRegistry`，一次两跳问答的 12 条消息连
  各自真实的时延与 token 数都标在图上；循环里那根不断变长的字符串用珊瑚色的自消息标出。
- **能动**：`step` 模式分 6 步推进（Play / Pause / Previous / Next / Replay，键盘 `←/→`、`Home/End`、空格、`R`），
  静态帧与"减少动效"下都是完整图形，禁用 JavaScript 也能看全图与全部文字。
- **能下钻**：图下三个页签（纯 CSS 切换，不用 JS）分别给出 ① **进入模型的请求体组成**（system 三条硬约束 /
  两行工具目录 / 输出格式约定 / 问题 / 采样参数，以及 native 模式的差别）② 两次 POST 的 prompt token、缓存命中
  与延迟 ③ 两个工具在两种协议下的写法与失败降级规则。
- 图上每个数字都取自 `runs/20260916_173649_live/react/` 的 `wire.jsonl` 与 `result.json`，重跑一次即可核对
  （序列图由 diagram-design 产出，Anthropic skin）。

## 目录结构

```
react-loop-mvp/
├── src/react_loop_mvp/
│   ├── react.py        # ★ 手写 ReAct 循环（28 行）+ prompt 模板 + 结果对象
│   ├── native.py       # 同一循环的 function calling 版本（对照）
│   ├── parsing.py      # 文本协议解析：Action / Action Input / Final Answer 与容错规则
│   ├── tools.py        # 工具注册表（一套 handler 服务两种协议）+ calculator + search_docs
│   ├── llm.py          # OpenAI 兼容 HTTP 客户端（重试/退避/原始字节留痕）+ 脚本化模型
│   ├── config.py       # 密钥/端点/模型解析（环境变量优先，密钥不进日志）
│   ├── trace.py        # JSONL 证据 + 控制台渲染（颜色、换行、NO_COLOR）
│   ├── cli.py          # 参数路由、产物落盘、退出码
│   └── data/           # kb.json（检索语料 9 篇）、demo_script.json（离线演示脚本）
├── tests/              # 187 个离线测试（另见 test_project_rules.py）
├── runs/               # 实测证据（已 gitignore，每次运行一个时间戳目录）
├── diagrams/           # 全链路时序图（自包含 HTML，含步进动画与纯 CSS 下钻页签）
├── SPEC.md             # 数据契约 / 协议契约 / 循环契约 / CLI 契约 / 决策记录
└── pyproject.toml      # 单文件配置：打包、pytest、ruff、console script
```

## 关键决策

| 决策 | 选择 | 为什么 |
| ---- | ---- | ------ |
| 依赖 | 零运行时依赖（stdlib） | 手写循环的意义就在于「一行都看得见」；引 SDK 会立刻把协议藏起来 |
| HTTP | `urllib.request` | `/chat/completions` 就是一次 JSON POST，一个函数就够；也顺手证明框架不是必需的 |
| 工具参数 | 每个工具只有一个字符串参数 | 文本协议表达不了类型化参数，索性不假装能；native 模式把同一个参数包成 JSON schema，两份描述指向同一个 handler |
| 循环保护 | 解析失败 3 次 / 同一动作连续 3 次即停 | 手写循环最大的风险是烧钱；两处保护共 9 行，换来可预测的成本上限 |
| 幻觉 Observation | 解析时截断 | 模型爱自己写 `Observation:`，一旦回填就是自欺；截断比提示词里加十遍「不要编造」更可靠 |
| 结果载体 | 一条变长的 user 消息 | ReAct 的原教旨形态，也是最能看清「上下文就是字符串」的形态 |
| 密钥 | 只读环境变量，`api_key` 不进 `repr`、日志只显示尾 4 位 | 仓库里永远不该出现密钥；`--json` / trace 里也做过断言 |
| 可测性 | 模型客户端是 Protocol，脚本化实现随包发布 | 循环能在无网络下被逐字节断言，同时这套假模型正好当离线演示 |
| 低版本兼容 | `requires-python >= 3.9`，关闭 ruff 的 `UP` 规则 | `typing.List/Optional` 保持 3.9 可解析；同族 MVP 也是 3.9+ |

## 验证

```bash
.venv/bin/pytest                     # 187 个测试，全部离线（不读 key、不发请求、不等时钟）
.venv/bin/ruff check .               # lint
.venv/bin/ruff format --check .      # 格式
.venv/bin/react-loop --fake           # 无 key 端到端冒烟
```

测试覆盖的边界（均为真实断言，不是样例跑通）：协议解析的 14 种写法（全角冒号、反引号、`name(arg)` 内联、
缺 Action Input、多行 Final Answer、中英关键字）、幻觉 Observation 截断、计算器的 9 类非法输入
（`__import__`、属性访问、超长表达式、超大指数、非有限结果）、检索排序（标题 > 标签 > 正文、平局按 id、
无命中不乱编）、注册表降级（未知工具、缺参数、工具抛异常、输出截断）、HTTP 客户端
（500/429 重试、401 不重试、非 JSON 响应、缺 choices、退避上限、原始字节留痕不含密钥）、
循环守卫（预算耗尽、死循环、连续解析失败、可恢复的单次失败、并行工具调用、usage 归集）、
CLI（退出码 0/1/2、stdin 取题、脚本缺失、产物齐备、`--json` 契约字段）、trace（颜色开关、NO_COLOR、
换行宽度、JSONL 追加语义）以及项目门禁（无框架 import、无企业商标字样、密钥文件不入库、README 与源码循环一致）。

Python 3.9.6（系统解释器）与 3.13.14（uv）均可运行：3.9 下跑 `PYTHONPATH=src python3 -m react_loop_mvp --fake`
可完整重放离线轨迹。

## 结论

1. **框架的两件事，三十行就能自己写**：写协议（工具目录 / JSON schema）与解析回复（正则 / `tool_calls`）。
   剩下的循环控制、工具执行、预算管理，与你日常写的任何循环没有区别。
2. **看得懂的循环可以很便宜**：同一道题，手写文本协议的输入 token 是原生方案的一半（927 vs 1,828），
   代价是不支持一轮多工具、参数只有字符串。
3. **该买的是便利，不是智能**：原生 function calling 用 79 行换来了并行工具调用与 provider 侧的格式保证；
   如果任务需要这两样，就该用它。这个判断只有在手写过一遍之后才做得出来。
4. **手写循环的杀手是「停不下来」**：两处保护（解析失败、重复动作）共 9 行，却是从 demo 到可用的分水岭；
   框架把这两件事藏进了抽象里，手写则逼你正面想清楚。

## 局限

- 一轮一个工具（`--mode react`），刻意保留 ReAct 原教旨形态；需要并行请用 `--mode native`。
- 检索工具是极简的关键词加权（标题 3 / 标签 2 / 正文 1 + 短语加分），只为让轨迹可复现，不是 RAG 方案。
- 无流式输出、无多轮会话记忆、无并发；单机单用户工具定位。
- `calculator` 只做纯算术（白名单 AST），不接 Python 环境；这是特性而非缺失。

## 复现要求

- macOS / Linux，Python 3.9+（仅标准库；测试另需 `pytest`、`ruff`）
- 真跑需要 `DEEPSEEK_API_KEY`（任意 OpenAI 兼容端点亦可，用 `--base-url` / `--model` 切换）
- 证据目录约定：一次运行一个时间戳目录，`trace.jsonl` 以追加方式写入（崩溃也留痕），要干净的证据请给它新目录
- `runs/` 已被 gitignore：README 引用的证据目录只存在于本地，用上面的命令可一条条重新生成（每条耗时 2–3 秒）
