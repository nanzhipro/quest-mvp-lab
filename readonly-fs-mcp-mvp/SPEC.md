# readonly-fs-mcp-mvp 技术方案 Spec

> 版本：v1.0
> 项目：`quest-mvp-lab/readonly-fs-mcp-mvp/`
> 语言/框架：Rust 2024 edition · 官方 MCP SDK `rmcp 3.4` · tokio
> 状态：已交付（64 测试 / 行覆盖率 98.85% / WorkBuddy stdio 形态接入）

本文件是实现方契约：只写"必须成立什么、怎么被验证、改一处要连带改哪里"，叙述与背景在 `README.md`。

---

## 1. 目标与非目标

**目标**：给本地 AI Agent（WorkBuddy 等 MCP 客户端）提供"只看不改"的工作目录访问能力，运行期指定唯一工作目录，只监听父进程管道。

**非目标**：写/改/移动/删除；搜索与正则；HTTP/SSE 传输；跨平台（Windows）字段映射；并发多客户端；权限代理（不做 sudo/越权读取）。

---

## 2. 硬约束

| 约束 | 判定标准 | 强制手段 |
| ---- | -------- | -------- |
| C1 只读 | 源码中不存在任何文件改写 API | `tests/guard_read_only.rs::source_contains_no_mutating_filesystem_api`（扫 `src/**/*.rs` 的**生产代码**，`MUTATING_APIS` 词表） |
| C2 只本机可达 | 不打开任何套接字 | 同一文件的 `source_contains_no_network_api`（`NETWORK_APIS` 词表）+ 传输仅 `rmcp::transport::stdio()` |
| C3 限域 | 每个工具接受的路径解析后必须落在根内 | `workspace::resolve_followed` / `workspace::locate` 统一判定；单测 `resolve_followed_refuses_escapes`、E2E `failures_arrive_as_tool_errors_with_stable_codes` |
| C4 无 unsafe | 源码无 `unsafe` | `source_contains_no_unsafe_code` |
| C5 无副作用 | 任意调用序列后工作目录逐字节不变 | E2E `no_call_ever_changes_the_workspace`（名称/类型/大小/权限/mtime/内容快照比对） |
| C6 只读注解 | 三个工具都声明只读注解 | `read_only_annotations_are_declared_for_every_tool` + E2E `publishes_three_read_only_tools_over_the_wire` |

C1 的扫描边界：每个 `src/*.rs` 文件中 `#[cfg(test)]` 之前的代码（测试夹具需要造文件）；一个文件出现多个 `#[cfg(test)]` 会直接测试失败（边界歧义）。

---

## 3. 工具契约（wire 形态）

工具名与新参数名是契约的一部分，客户端（及模型的提示）按此调用。

### 3.1 `list_directory`

入参（`inputSchema`，`additionalProperties: false`）：

| 字段 | JSON 类型 | 必填 | 默认 | 语义 |
| ---- | --------- | ---- | ---- | ---- |
| `path` | string \| null | 否 | `null`（= 根） | 相对根解析；绝对路径必须落在根内；允许 `.`/`..`，只要解析后仍在根内 |
| `include_hidden` | boolean | 否 | `false` | 假时不列 `.` 开头条目（递归时点目录整棵跳过） |
| `recursive` | boolean | 否 | `false` | 真时按 `max_depth` 递归；**任何情况下不跟随符号链接进入目录** |
| `max_depth` | integer \| null | 否 | `2` | 层级数：1 = 仅直接子项，2 = 含其子项；夹到 `≤ limits.max_depth`；`recursive=false` 时忽略 |
| `max_entries` | integer \| null | 否 | `200` | 条目预算；夹到 `≤ limits.max_entries` |

出参（`outputSchema` / `structuredContent`）：

| 字段 | 类型 | 语义 |
| ---- | ---- | ---- |
| `root` | string | 根的绝对路径 |
| `path` | string | 被列目录的根相对路径（根为 `"."`） |
| `entries[]` | object[] | BFS 序；同层按文件名 byte 序；字段 `name`/`path`（根相对，可直接喂给其它工具）/`kind`（`file`\|`directory`\|`symlink`\|`other`）/`size_bytes`（条目自身大小；符号链接为链接长度）/`modified`（RFC3339 UTC 或 null）/`depth`（0 = 直接子项） |
| `entry_count` | integer | `entries` 长度 |
| `truncated` | boolean | 真 = 因预算停止，仍有未返回条目 |
| `unreadable[]` | string[] | 因权限未能读取的子目录（根相对），用于区分"缺失"与"没有" |

失败：`outside_workspace`（解析后越界/符号链接出域）、`not_found`、`not_a_directory`、`permission_denied`（被请求目录本身不可读时直接报错；嵌套目录不可读进 `unreadable[]`）、`invalid_parameter`。

### 3.2 `read_file`

| 字段 | JSON 类型 | 必填 | 默认 | 语义 |
| ---- | --------- | ---- | ---- | ---- |
| `path` | string | 是 | — | 必须解析为根内普通文件 |
| `start_line` | integer \| null | 否 | `1` | 1-based；`0` → `invalid_parameter` |
| `max_lines` | integer \| null | 否 | `200` | 夹到 `≤ limits.max_read_lines` |

出参：

| 字段 | 类型 | 语义 |
| ---- | ---- | ---- |
| `root` / `path` | string | 同上 |
| `size_bytes` | integer | 文件总大小 |
| `total_lines` | integer \| null | 读到文件末尾时给准确值；因预算提前停止时为 `null`（不猜） |
| `start_line` | integer | 实际起始行（回显，1-based） |
| `end_line` | integer \| null | 最后一行行号；空结果时为 `null` |
| `returned_lines` | integer | `content` 行数 |
| `truncated` | boolean | 真 = 文件在 `end_line` 之后还有内容（翻页：`start_line = end_line + 1`） |
| `content` | string | 请求行以 `\n` 连接，无尾随换行；空区间为空串 |

语义细节（均有用例）：

- 行分割按 `\n`；CRLF 行尾的 `\r` 会被去掉；文件以 `\n` 结尾不计空行；空文件 0 行。
- 单行超过 `max_read_bytes` 且尚未收集到任何行 → `line_too_large`（避免"空页 + 反复重试"）。
- 已收集行数达到预算或再加一行会超字节预算 → 停止并置 `truncated: true`。
- 非 UTF-8 或含 NUL 字节 → `not_text`（message 带首个非法行号）。
- `start_line` 超过文件末尾 → 成功返回空 `content`、`returned_lines: 0`、`end_line: null`。

失败：`outside_workspace`、`not_found`、`not_a_file`（目录/设备/套接字）、`not_text`、`line_too_large`、`invalid_parameter`、`permission_denied`。

### 3.3 `file_metadata`

入参：`path`（string，必填）。

出参：

| 字段 | 类型 | 语义 |
| ---- | ---- | ---- |
| `kind` | string | `file`\|`directory`\|`symlink`\|`other`（`lstat` 视角） |
| `size_bytes` | integer | 条目自身大小（符号链接 = 链接目标串长度） |
| `modified` / `created` | string \| null | RFC3339 UTC，文件系统未记录时为 `null` |
| `mode_octal` / `permissions` | string | 如 `"0640"` / `"rw-r-----"`（含 setuid/setgid/sticky 的 12 位量，符号位只表达 rwx） |
| `uid` / `gid` | integer | 属主/属组 |
| `hard_links` / `inode` | integer | 硬链接数 / inode 号 |
| `symlink_target` | string \| null | **仅当**条目是符号链接且目标落在根内时给出（根相对）；目标在根外或悬空为 `null` |
| `escapes_workspace` | boolean | 真 = 符号链接目标在根外（目标路径因此不披露） |
| `looks_like_text` | boolean \| null | 可读普通文件（或其根内目标）前 4 KiB 为合法 UTF-8 且无 NUL；非普通文件为 `null` |

失败：`outside_workspace`、`not_found`、`permission_denied`、`invalid_parameter`。

### 3.4 错误载荷

工具级错误 = `isError: true` 的 `CallToolResult`，`content[0]` 是 `structuredContent` 的 JSON 文本（保证忽略结构化字段的客户端也能读到原因）：

```json
{"code": "outside_workspace", "message": "`../secret.txt` resolves outside the workspace root `/tmp/x`; only paths inside the root can be inspected"}
```

`code` 取值即 §5 表；参数反序列化失败（未知字段、类型不符）由框架产出 JSON-RPC `invalid_params`，这是协议层错误，不是工具级错误。

---

## 4. 路径解析规则

```
candidate(requested) =  requested 为空 → invalid_parameter
                        requested 含 '\0' → invalid_parameter
                        requested 绝对 → requested
                        否则 → root.join(requested)

resolve_followed(requested):              # 读内容（read_file / list_directory 的目标目录）
    p = canonicalize(candidate)           # 失败 → not_found / permission_denied
    assert p.starts_with(root)            # 失败 → outside_workspace
    return p

locate(requested):                        # 看条目本身（file_metadata）
    md = lstat(candidate)                 # 内核已解析 `.`/`..` 与父链符号链接
    if md 是符号链接:
        cp = canonicalize(candidate.parent())   # 父链必须在根内
        assert cp.starts_with(root)             # 否则 outside_workspace
        entry = cp.join(candidate.file_name())
        target = canonicalize(entry) 通过且 starts_with(root) ? Some : None
        escapes = target 解析成功但落在根外
    else:
        p = canonicalize(candidate); assert p.starts_with(root)
        entry = p; target = None; escapes = false
```

判定一律在 **canonical 路径**上做前缀比较（不是字符串比较、不是 lexically normalized），因此：`..` 逃逸被拒；根内符号链接指向根外被拒（读取）或在元数据里被标为 `escapes_workspace`（不泄露目标）；根内符号链接指向根内正常工作。

---

## 5. 错误码表

| code | 常量（`src/error.rs`） | 触发条件 |
| ---- | --------------------- | -------- |
| `outside_workspace` | `CODE_OUTSIDE_WORKSPACE` | 解析后落在根外 |
| `not_found` | `CODE_NOT_FOUND` | `ENOENT` |
| `not_a_file` | `CODE_NOT_A_FILE` | 期望普通文件却给了目录/设备/FIFO/套接字 |
| `not_a_directory` | `CODE_NOT_A_DIRECTORY` | 期望目录却给了文件 |
| `permission_denied` | `CODE_PERMISSION_DENIED` | `EACCES` |
| `not_text` | `CODE_NOT_TEXT` | 非法 UTF-8 或含 NUL（带行号） |
| `line_too_large` | `CODE_LINE_TOO_LARGE` | 单行超过本次字节预算 |
| `invalid_parameter` | `CODE_INVALID_PARAMETER` | 空路径、含 NUL 的路径、`start_line = 0` |
| `io_error` | `CODE_IO` | 其余 I/O 失败（附 `source`） |

新增错误必须同时：加常量 + 加 `FsError` 变体与 `code()` 分支 + 在 `error.rs` 单测的样例表里出现 + 更新 §5 与 README 错误码表 + 在对应工具的 `description` 里列出该 code。

---

## 6. CLI 契约

```
readonly-fs-mcp [--root DIR] [--max-read-lines N] [--max-entries N]
                [--max-depth N] [--max-read-bytes BYTES] [--max-scan-bytes BYTES]
```

| 参数 | 默认 | 校验 |
| ---- | ---- | ---- |
| `--root` | `.`（当前目录） | 启动时 `canonicalize`，失败或非目录 → 退出码 2 |
| `--max-read-lines` | 2000 | `< 1` 抬到 1 |
| `--max-entries` | 2000 | 同上 |
| `--max-depth` | 8 | 同上 |
| `--max-read-bytes` | 524288 | 同上 |
| `--max-scan-bytes` | 8388608 | 同上 |

行为契约：

- stdout **只有** JSON-RPC 行（启动横幅、诊断一律 stderr）。
- stdin 关闭 → 正常退出，退出码 0。
- 退出码 2 = 启动失败（`cannot use ... as the workspace root`）。
- 工具在 `initialize` 阶段通过 `instructions` 告知模型：根路径、路径规则、"不存在任何写动词"、以及各项预算。

---

## 7. 预算与夹取（clamp）规则

| 预算 | 调用方可调 | 服务端夹取 | 超限表现 |
| ---- | ---------- | ---------- | -------- |
| `read_file.max_lines` | 是（≤ 上限） | `clamp(1, limits.max_read_lines)` | `returned_lines` 达上限 + `truncated: true` |
| `read_file` 字节 | 否 | `limits.max_read_bytes` | 停止收集 + `truncated: true`；单行超限且无内容 → `line_too_large` |
| `list_directory.max_entries` | 是 | `clamp(1, limits.max_entries)` | `truncated: true`（只在确实有下一条时置位，不误报） |
| `list_directory.max_depth` | 是（仅递归时） | `clamp(1, limits.max_depth)` | 不再下探 |
| 文件扫描（`total_lines`） | 否 | 文件 ≤ `limits.max_scan_bytes` 时读到末尾 | 超过则 `total_lines: null` |

夹取是"静默夹取 + 显式标记"，不产生错误：模型从 `truncated`/`total_lines` 就能判断还需不需要翻页。

---

## 8. ADR

| # | 决策 | 备选 | 结论与理由 |
| - | ---- | ---- | ---------- |
| 1 | 官方 Rust SDK `rmcp` | 手写 JSON-RPC | 类型派生 schema（代码即定义）、协议版本协商与 `structuredContent` 由 SDK 保证；少写一层易漂移的解析代码 |
| 2 | stdio 传输 | Streamable HTTP（127.0.0.1） | "仅本机可访问"的强形态是没有 socket；HTTP 引进端口占用、鉴权与并发面，对单机读文件无收益 |
| 3 | 工具级错误（`isError` + `code`） | JSON-RPC 协议错误 | 协议错误对模型是黑盒；工具级错误把"工具跑过、答案是不行"连原因带码一起给模型 |
| 4 | `deny_unknown_fields` | 忽略未知字段 | 拼错参数名时静默按默认值执行会给出**错误答案**；显式失败更安全 |
| 5 | 读用 followed / 元数据用 lstat | 统一跟随 | 读必须证明真实目标在域内；元数据要回答"这个条目本身是什么"，否则符号链接无法被观察 |
| 6 | 递归不跟随符号链接 | 跟随 + 环检测 | 跟随同时引入越界与环两类风险；列出符号链接条目已足够模型决策 |
| 7 | 非 UTF-8 直接拒绝 | lossy 解码 | 乱码比拒绝更危险（模型会当成真实内容）；`looks_like_text` 提前给出可否读 |
| 8 | 同步文件 I/O 跑在 tokio 上 | `spawn_blocking` | 单客户端、读量有预算；换来的是没有跨任务的顺序/取消语义复杂度 |
| 9 | 守卫测试扫源码 | 只靠 code review | 只读是**产品承诺**，必须能被 CI 证伪；词表数据化，新增 API 时改词表即可 |

---

## 9. 变更协议（改一处必须连带改）

| 若改动 | 必须同步 |
| ------ | -------- |
| 工具名 / 参数名 / 结果字段 | `src/tools.rs` 类型 + `src/server.rs` 的 `description` + README「工具契约」表 + SPEC §3 + `tests/mcp_stdio.rs` 的断言 |
| 新增错误码 | `src/error.rs`（常量+变体+`code()`+单测样例）+ SPEC §5 + README 错误码表 + 对应工具 `description` |
| 新增文件系统 API | God 守卫词表（`tests/guard_read_only.rs`）需评估：若属改写类 API 则不得引入 |
| 新增 CLI 参数 | `src/cli.rs` + `Limits` + README「命令与退出码」+ SPEC §6 + `cli.rs` 单测 |
| 预算默认值/上限 | `Limits::default()` + `src/cli.rs` 默认值 + README 表 + SPEC §7 |
| 工具数量（新增只读工具） | 守卫测试里 `#[tool(` 计数（3）+ README 能力表 + SPEC §3 |

---

## 10. 验收清单

| # | 验收 | 证据 |
| - | ---- | ---- |
| 1 | 3 个工具在线且只读注解齐全 | `tests/mcp_stdio.rs::publishes_three_read_only_tools_over_the_wire` |
| 2 | 三个工具的成功返回可反序列化回契约类型 | `tests/mcp_stdio.rs::listings_reads_and_metadata_round_trip_as_typed_payloads` |
| 3 | 越界/类型/参数错误均为带码的工具级错误 | `tests/mcp_stdio.rs::failures_arrive_as_tool_errors_with_stable_codes` |
| 4 | 不存在的写/删工具无法被调用 | `tests/mcp_stdio.rs::an_unknown_tool_is_a_protocol_error` |
| 5 | 任意调用序列后工作目录零变化 | `tests/mcp_stdio.rs::no_call_ever_changes_the_workspace` |
| 6 | 工作目录只读（`chmod 555`）下读取仍成功 | `tests/mcp_stdio.rs::reads_succeed_on_a_write_protected_workspace` |
| 7 | 非法 `--root` 退出码 2、stdout 为空 | `tests/mcp_stdio.rs::a_root_that_cannot_be_used_fails_loudly` |
| 8 | stdout 只有 JSON-RPC、关闭 stdin 自行退出 | `tests/mcp_stdio.rs::stdout_carries_json_rpc_lines_only` |
| 9 | 源码无改写 API / 无网络 API / 无 unsafe | `tests/guard_read_only.rs`（4 项） |
| 10 | README 与实现一致（工具名、`--root`） | `tests/guard_read_only.rs::the_readme_documents_the_three_tools` |
| 11 | 行覆盖率 ≥ 95% | `./scripts/verify.sh` → `runs/<ts>/verify.log` |
| 12 | 格式与 lint 干净 | 同上（`cargo fmt --all --check`、`cargo clippy --all-targets -- -D warnings`） |

## 11. 实测数据（2026-09-23）

| 项目 | 数值 |
| ---- | ---- |
| 行覆盖率（llvm-cov lines） | 1388 行 / 未覆盖 16 行 = **98.85%** |
| 区域覆盖率（regions） | 2416 / 未覆盖 53 = 97.81% |
| 函数覆盖率（functions） | 125 / 未覆盖 7 = 94.40% |
| 测试 | 49 单元 + 7 守卫 + 8 端到端 = **64**，全绿 |
| 二进制大小（release，strip + thin LTO） | 3,405,184 B（约 3.2 MiB） |
