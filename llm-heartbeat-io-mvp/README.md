# llm-heartbeat-io-mvp — LLM 心跳消息的输入输出全量捕获

> **Capture the exact bytes a client sends to an LLM for a minimal heartbeat (`ping`) and the exact bytes the model returns — then dissect both sides.**
> 用本地录制代理抓住「一句话心跳」在两端的全量数据：请求体、响应体、token 账单、系统提示词解剖，并对照裸模型调用。

![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Deps: stdlib only](https://img.shields.io/badge/deps-stdlib%20only-brightgreen.svg)
![Model: deepseek-v4-flash](https://img.shields.io/badge/model-deepseek--v4--flash-orange.svg)
![Captured: 7 runs · 200+ exchanges](https://img.shields.io/badge/captured-7%20runs%20%C2%B7%20200%2B%20exchanges-lightgrey.svg)

## Features

* **字节级录制**：本地反向代理把每个 HTTP 请求体、响应体原样落盘（`raw/req_*.json`、`raw/resp_*.bin`），含 SHA-256 与耗时
* **两层对照**：同一句 `ping`，既抓 **Agent 完整负载**（Hermes），也抓 **裸模型调用**（curl，仅 `model` + `messages`）
* **系统提示词解剖**：按区块统计字符数，并给出 CJK 感知的 token 估算（与厂商 `usage` 对账，误差 +2.0%）
* **令牌账单以厂商为准**：`prompt_tokens` / `completion_tokens` / `reasoning_tokens` / `cache_hit` / `cache_miss` 全部取自响应 `usage`，不靠猜
* **受控 A/B**：唯一变量为「工作区上下文可达 / 不可达」，量化 AGENTS.md 注入的字符与 token 代价
* **零第三方依赖**：只用 Python 标准库；不改动 `~/.hermes` 任何真实文件

## 背景

Agent 的每一次「心跳」（哪怕用户只发一个 `ping`）背后都是一份几百 KB 级别的可执行负载：系统提示词、工具 schema、记忆、项目上下文、技能目录。这些内容平时只存在于 API 请求体里，没人看过它的全貌，也没人算过它的账单。

本项目回答四个问题：

1. 一次最小心跳，客户端**到底发了什么**（结构、字节数、token 数）？
2. 这份负载里，**谁是成本大头**？
3. 模型**返回了什么**（内容、推理、结束原因、流式开销）？
4. 同样的 `ping`，**裸模型调用**与**Agent 调用**差多少？

## 工作原理

```
client (hermes chat / curl)
      │  HTTP
      ▼
capture_proxy.py  ──►  raw/req_*.json   （原样落盘，Authorization 脱敏）
      │  HTTPS
      ▼
api.deepseek.com  ──►  raw/resp_*.bin   （原样落盘，SSE 或 JSON）
```

两个关键工程决定：

- **为什么用反向代理而不是客户端日志**：只有代理能同时拿到「客户端发出的原始字节」和「服务端返回的原始字节」，且与客户端实现（Hermes、SDK、curl）解耦。
- **为什么要镜像 `HERMES_HOME`**：Hermes 用 `load_dotenv(~/.hermes/.env, override=True)` 加载配置，进程环境变量覆盖不了 `DEEPSEEK_BASE_URL`。`run_heartbeat.sh` 因此建一个临时 home，把 `~/.hermes/*` 全部符号链接过去（技能、记忆、`config.yaml`、`state.db`、MCP token 都保持一致），只把 `.env` 复制一份并把 base URL 指向代理。**真实 profile 不被修改，负载内容与真实运行等价**。

## 快速开始

```bash
# 一次完整抓取：H1 Agent 心跳 + M1/M2 裸模型心跳（非流式 / 流式），然后自动解剖
./run_heartbeat.sh

# 受控 A/B：工作区上下文可达 vs 不可达（其余全部相同）
./ab_ctx.sh

# 多轮会话抓取（带工具调用 / skill 加载）：同一会话内多轮，逐轮渲染
./capture_conversation.sh /path/to/workdir "第一个问题" "第二个问题"

# 逐轮打印会话的 请求/响应 JSON（生成 conversation*.json / session_messages.json）
python3 show_conversation.py runs/<timestamp>

# 已有 run 目录可单独重新解剖
python3 dissect.py runs/<timestamp>

# 逐字段打印输入 JSON / 输出 JSON 的结构与数据（生成 io_view.txt + assembled_<seq>.json）
python3 show_io.py runs/<timestamp>
```

产物（每次运行独立时间戳目录，不覆盖历史）：

```
runs/<YYYYmmdd_HHMMSS>/
├── events.jsonl        # 每个 HTTP 交换一条记录（请求 + 响应配对）
├── raw/                # req_0001.json / resp_0001.bin 等原始字节 + 头部快照
├── summary.json        # 交换级摘要（字符数、token、耗时）
├── dissect.json        # 深度解剖结果（提示词区块、工具表、usage）
├── dissect.txt         # 人类可读报告
├── io_view.txt         # 输入/输出 JSON 的结构与数据逐字段打印
├── conversation.json   # 多轮会话全量（每轮完整 system prompt + 全部 messages）
├── conversation_view.json # 展示视图（system prompt 用标记替代，只列每轮新增消息）
├── session_messages.json  # 会话最终上下文的完整 messages 数组
└── assembled_0014.json # 把 SSE 帧还原成的「逻辑响应 JSON」
```

## 实测结果

### 同一句 `ping`，两端的差距

| 指标 | Agent 心跳（Hermes） | 裸模型心跳（curl） | 倍数 |
| ---- | -------------------- | ------------------ | ---- |
| 请求体 | 154,340 B | 90 B | 1,715× |
| 消息数 | 2（system 75,851 字符 + user 4 字符） | 1（user 4 字符） | — |
| 工具 schema | 29 个，62,387 字符 | 0 | — |
| `prompt_tokens` | **38,718** | **31** | **1,249×** |
| `completion_tokens` | 58（其中推理 35） | 67（其中推理 53） | — |
| 端到端耗时 | 2,928 ms | 1,659 ms | 1.76× |
| 输出内容 | `pong — Hermes online, cwd /Users/nanzhi/workspace. What's next?`（63 字符） | `pong 🏓 What can I help you with?`（33 字符） | — |

用户那 4 个字符（`ping`）只占请求体的 **0.0029%**。

### 输入构成（Agent 心跳，按 token 估算并与厂商 usage 对账）

| 区块 | 字符数 | 估算 tokens | 占比（字符） |
| ---- | ------ | ----------- | ------------ |
| 技能目录 `<available_skills>` | 32,801 | ~10,106 | 43.2% |
| 项目上下文 `AGENTS.md` / `CLAUDE.md` | 24,719 | ~6,420 | 32.6% |
| 核心操作指令（身份、执行纪律、平台说明） | 13,720 | ~3,518 | 18.1% |
| 记忆 + 用户画像 + Mem0 | 4,611 | ~1,933 | 6.1% |
| 工具 schema（29 个） | 62,387 | ~17,519 | — |
| **合计** | **138,242** | **~39,497** | 估算误差 **+2.0%**（厂商实际 38,718） |

工具 schema 里最贵的是 `tool_search`（10,897 字符，其中描述 10,124 字符）——它把 97 个延迟加载的工具压成一个索引入口，这是 29 而不是 126 个工具出现在请求里的原因。

### 受控 A/B：工作区上下文值多少 token

唯一变量为「工作区上下文是否可达」（`TERMINAL_CWD` 指向 `workspace` vs 取消并切到 `/tmp`）：

| 变量 | 有工作区上下文 | 无工作区上下文 | 差值 |
| ---- | -------------- | -------------- | ---- |
| 系统提示词 | 75,851 字符 | 50,634 字符 | +25,217 字符 |
| 请求体 | 154,340 B | 128,310 B | +26,030 B |
| `prompt_tokens` | 38,718 | 32,267 | **+6,451（+20.0%）** |
| 两份提示词公共前缀 | — | — | 仅 9,496 字符 |

顺带发现：shell 里 `cd /tmp` **不会**改变 Agent 的工作区——`TERMINAL_CWD` 才是运行时载体，进程环境变量优先于 `chdir`。第一次 A/B 两轮结果字节完全相同，就是踩了这个坑。

### 一次心跳不只有一次请求

| 交换 | 性质 | 结果 |
| ---- | ---- | ---- |
| #1–#12 | 启动期模型发现探测（`/api/v1/models`、`/api/tags`、`/v1/props`、`/models`、`/api/show` …） | 12 次，合计 1,989 ms，其中 5 次 404 |
| #13 | 会话标题生成（带 `json_schema` 结构化输出） | **400** `This response_format type is unavailable now` |
| #14 | **主心跳**（Agent 回合） | 200，154,340 B 请求 / 18,772 B 响应 / 59 个 SSE 事件 |
| #15 | 标题生成重试（去掉 `response_format`） | 200，产出 `{"title": "Respond to ping message"}` |
| #16 / #17 | 裸模型心跳（非流式 / 流式） | 200 |

即：**用户发一个 `ping`，客户端实际发出 17 次 HTTP 请求、3 次真正的推理调用**（主回合 + 标题生成 2 次尝试），主回合之外还有 259 个输入 token 用于起标题。

### 前缀缓存的实测效果

同一份 154,340 B 负载在 3 分钟内重复发送：

| 情形 | `cache_hit` / `prompt_tokens` | 命中率 |
| ---- | ----------------------------- | ------ |
| 首次发送（冷前缀，命中止于首个差异字节前） | 2,560 / 38,718 | 6.6% |
| 完全相同负载重发 | 38,528 / 38,718 | **99.5%** |

注意 payload 完全一致时才吃满缓存；受控 A/B 里两份提示词的公共前缀只有 9,496 字符，缓存立刻退化。

### 输出侧的两个观察

1. **推理预算不随上下文膨胀**：Agent 心跳的 `reasoning_tokens` 只有 35（裸调用反而 53）。大负载没有换来更多思考，只换来一个更"自知"的回答——它读到了注入的 cwd 并写进了回复。
2. **裸调用的推理文本出现异常插入**：两次独立采样（非流式 / 流式）在同一位置出现不属于语义的 token——

   - `This is a2026年5月12日, a simple connectivity check.`
   - `This is aPython a simple connectivity check.`

   两段都通过 `json.loads` 校验、UTF-8 合法、原始字节已存盘（SHA-256 可复核），因此不像传输损坏；疑似模型侧解码噪声，值得继续跟踪。

## 结论

1. **Agent 的固定成本是杠杆结构，不是常数**：一句 `ping` 的账单是 38,718 输入 token，其中 89.6% 由系统提示词与工具 schema 占据，用户内容占 0.003%。优化点是「技能目录 + 项目上下文 + 工具 schema」三项，不是对话内容。
2. **工具检索是省钱设计**：`tool_search` 用 10,897 字符的索引替换了约百个工具的全量 schema；若不延迟加载，输入会再膨胀数倍。
3. **项目上下文有明确价格**：本工作区的 `AGENTS.md`/`CLAUDE.md` 单次注入 25,217 字符 ≈ 6,451 token（+20%），每个回合都付。
4. **缓存是唯一的大幅折扣**：前缀完全一致时命中 99.5%，出现任何早段差异（如 cwd 行）立即退化到 6.6%。
5. **一致性提醒**：同一句 `ping`，裸调用与 Agent 调用的输出内容不同（后者把 cwd 写进回复），说明被测对象不是「模型」而是「模型 + 负载」。

## 沉淀为 Skill

本套脚本已固化为 Skill `llm-wire-capture`（`~/.hermes/skills/autonomous-ai-agents/llm-wire-capture/`）：`scripts/` 是自带的便携副本（换机器/换 agent 可直接跑），`references/` 记协议事实、客户端接入配方与量级基线。两边改动请同步复制，不要只改一边。

自验证基线：`runs/20260911_152649_skill_selfverify/` —— 用 skill 自带的 `capture_conversation.sh` 跑两轮小目录问答，得到 6 次推理调用 / 3 次工具调用（`search_files`+`terminal`+`read_file`）/ 129,569 prompt tokens，四件会话产物齐备。

## 可视化

> 仓库不收录 `runs/`，也不收录由它派生的交互报告（`message-assembly.html` 内嵌真实请求/响应负载）。
> 随仓库发布的是**生成器 + 静态导出**；克隆后按本节最后一条命令即可在本地重建交互版。

- `diagrams/message-assembly.html` —— **交互版主产物**（本地生成、不入库）：五轮完整 Agent Loop（POST #1–#5，messages 2 → 5 → 7 → 9 → 11）的时序列图 + 逐轮回放列表。每条消息可点开看它真实的 payload（原始消息 JSON、tool_calls 参数、工具返回的解析体）；已发送过的消息折叠成回指链接。步进用 diagram-design 的规范控制器（逐字拷贝），静态帧、无 JS、打印、减少动态效果下全部内容完整可见。
- `diagrams/message-assembly.png`（2560×2000，透明底）/ `.svg`（矢量）—— 从同一份 HTML 导出的静态图：左带消息交换、右带每轮 `messages[]`（新增＝深色描边＋左侧色条，已发＝细灰行）。五轮真实账单：132,889 → 141,740 → 153,200 → 155,737 → 157,428 B。
- `diagrams/build_explorer.py` —— 生成器：从 `runs/20260911_150720_conversation/` 读抓包，产出上面这份交互 HTML（数据驱动，改抓包重跑即可）。
- `diagrams/render_png.py`、`export_svg.py` —— 无 Playwright 的 PNG 渲染与 SVG 导出路径。

## 复现要求

- macOS / Linux，Python 3.9+（仅标准库）
- 本机已安装并配置 Hermes（`~/.hermes/.env` 含 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`）
- 代理监听 127.0.0.1:8899，可换端口：`./run_heartbeat.sh 9000`
- 重建交互报告：`python3 diagrams/build_explorer.py runs/<run-dir> diagrams/message-assembly.html`
  （读该 run 的抓包；渲染控制器取自本机 `~/.agents/skills/diagram-design` 的模板）
