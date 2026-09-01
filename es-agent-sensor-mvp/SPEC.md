# ES Agent Sensor MVP 技术方案 Spec

> 版本：v1.0（含实测修正案）
> 日期：2026-09-01
> 项目：`quest-mvp-lab/es-agent-sensor-mvp/`
> 状态：已交付（86 单测 / 行覆盖 96.71% / e2e PASS / Hermes 冒烟 1749 事件零丢弃）

---

## 1. 背景与目标

AI Agent（以 Hermes Agent 为代表，工作目录 `/Users/nanzhi/.hermes`）在本机执行大量自主行为：派生子进程（kernel runner、MCP server）、读写配置与缓存、SQLite 事务（`kanban.db-wal`）、tmp+rename 原子写、Unix Socket IPC、对外网络连接。要回答「Agent 在机器上做了什么」，需要内核级数据源 + 行为级归因 + 归一化事件输出。

**目标**：用 Rust + macOS Endpoint Security Framework（**仅 NOTIFY 事件**）构建 Agent 行为感知传感器：

- 命令行指定一个或多个 Agent 工作目录（`--agent id=path`，可多次），同时感知多个 Agent；
- 感知 Agent 的**文件、进程、IPC（Unix Socket）、网络连接**行为；
- 输出归一化 **AgentEvent JSONL**（stdout 或 `--output` 文件），一条事件即可读清「谁在什么时候对什么目标执行了什么操作、附带什么数据、产生什么行为」；
- 事件订阅需审慎权衡：订太多性能卡顿，订太少感知缺失；
- 工程化：组件化、≥90% 单测覆盖、端到端自动化验收、高稳定性。

**非目标**：行为管控/拦截（不订阅 AUTH 事件，无 deadline 压力与内核授权缓存复杂度）；XPC/mach IPC 与 TCP/UDP 的 ESF 原生观测（ESF 盲区，见 §7 补偿与限制）。

## 2. 总体架构

```
                        ┌────────────────────────────────────────────┐
                        │              Kernel (EndpointSecurity)     │
                        └──────────────────┬─────────────────────────┘
                                           │ es_message_t（系统级，14 个 NOTIFY 事件）
                                           ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │ csrc/es_shim.c（唯一接触 ES 头文件处）                                │
  │   blocks 桥接 → 字段拷贝到扁平 C 结构体（tagged union）→ 立刻返回      │
  └─────────────────────────────────────────────────────────────────────┘
                                           │ essh_message_t（值拷贝，~26KB）
                                           ▼
                有界 channel（8192，背压：满则丢 + dropped 计数）
                                           ▼
  ┌──────────────────────────── 单写线程 ───────────────────────────────┐
  │ decode（C 结构 → verb/category/target + TreeOp）                    │
  │ proctree（Santa 移植：(pid,pidversion) 键进程树，fork/exec/exit     │
  │   生命周期，agent 注解传播）                                         │
  │ matcher（双向匹配：actor_process ∪ target_path）                    │
  │ assemble → AgentEvent（serde）→ JsonlSink（stdout|文件，写即 flush） │
  └─────────────────────────────────────────────────────────────────────┘
        ▲
  net-poller 线程（--net-poll-interval，默认 2s）：
  对 agent 进程树成员做 libproc socket 快照 diff → net_connect/net_close
```

关键工程决策：

- **不用任何 ES crate / bindgen**：`es_message_t` 布局大且随 message version 演进，手写 `repr(C)` 风险高；`es_handler_block_t` 的 blocks 语法纯 Rust 无法表达。C shim 用 `cc` 编译（`-fblocks`），链接 `EndpointSecurity`/`bsm`/`proc` dylib。此模式已在 `es-mvp` 验证。
- **回调零阻塞**：ES 回调里只做字段拷贝；过滤、归一化、序列化全部在写线程。
- **多 Agent 共用一个 es_client**：一次订阅，过滤器匹配 N 个目录。
- **unsafe 收敛**：仅在 `ffi.rs`（FFI 安全包装）与 `sysproc.rs`（libproc/mach/sysctl 封装），每处带 SAFETY 注释；业务层经 `EsBackend`/`ProcSource` trait 注入，单测零 root 依赖（MockEs / FakeSource）。

## 3. ES 事件订阅决策（行为反推）

### 3.1 行为 → 事件映射

| Agent 行为 | 订阅事件（NOTIFY） | 取舍理由 |
|---|---|---|
| 启动/派生进程 | `EXEC` `FORK` `EXIT` | 进程树归因骨架；EXIT 驱动延迟回收防泄漏 |
| 写文件 | `CLOSE`(modified) `CREATE` `TRUNCATE` | **不订 `WRITE`**：每次 write(2) 一条事件，SQLite/日志场景事件量爆炸；`CLOSE.modified=true` 是去重后的写完成信号（Santa/osquery 标准做法） |
| 读文件 | `OPEN` | fflag 给出 read/write/readwrite 意图；感知「Agent 看了什么」的唯一途径 |
| 移动/原子写 | `RENAME` | config/history 等 tmp+rename 模式的关键证据 |
| 删除 | `UNLINK` | **macOS ES 无 RMDIR 事件**（实测修正案①），rmdir 归并 UNLINK，按 stat mode 区分 file/dir |
| 硬链/克隆 | `LINK` `CLONE` | 低频低价，补全文件操作面 |
| 属性/xattr 变更 | `SETATTRLIST` | chmod/quarantine 等，低频 |
| IPC | `UIPC_BIND` `UIPC_CONNECT` | **ESF 不暴露 XPC/mach IPC**；Unix Socket 是可观测 IPC 全部（实测修正案④：UIPC_CONNECT 见 §7） |

**默认订阅集 = 14 个 NOTIFY 事件**（`EV_DEFAULT_MASK = (1<<14)-1`）。

**明确不订**：`WRITE`（极高频）、`READDIR`/`CHDIR`/`FSGETPATH`（高频低值）、`SIGNAL`、`PROC_CHECK`、`MMAP`、全部 `AUTH_*`。`--extra-events write,readdir,signal` 可显式加订（默认关闭）。

**参照系**：es-procattr-mvp 订 9 个事件系统级 80 秒仅 1040 条；本方案实测 1500~6500 事件/秒（随系统负载），NOTIFY 无 deadline，有界 channel 背压下零丢弃。

## 4. 进程树（移植 Google Santa ProcessTree）

参考 `santa/Source/common/processtree/` 生产实现：

- **键**：`(pid, pidversion)` 二元组（pidversion 取自 `audit_token_to_pidversion`），天然解决 pid 复用——同 pid 不同 incarnation 是不同节点。
- **结构**：平铺 `HashMap<PidKey, Arc<Process>>` + 节点内单向 `parent: Arc<Process>` 父链，不维护子链；Arc 保证已退出父节点的祖先链仍可遍历（替代 Santa 的 shared_ptr + refcount/tombstone）。
- **节点**：`pid`、`cred(uid/gid)`、`program: Arc<Program>{executable, argv, signing_id, team_id, cdhash, is_platform_binary}`、`agent_id` 注解。
- **生命周期**：
  - FORK：构造子节点，**共享**父的 `Arc<Program>`（零拷贝继承），注解沿树传播；
  - EXEC：**新建节点而非原地更新**（pidversion 变），继承旧节点 parent，替换 program；旧 key 进延迟回收堆；
  - EXIT：不立即删，进最小堆 **~5s grace 延迟回收**（以已见最新事件 mach_time 为基准），避免滞后事件找不到进程；
  - 去重：完整事件身份 `(mach_time, kind, actor, other)` 有界 LRU（16384，插入序老化），**只丢精确重复，不丢乱序新事件**。
- **Seeding/Backfill**：启动时 `proc_listpids` 枚举现存进程 → 每 pid 用 `task_info(TASK_AUDIT_TOKEN)` 取 pidversion/cred、`proc_pidpath` 取路径、`sysctl(KERN_PROCARGS2)` 取 argv、`csops` 取签名标志、`proc_pidinfo(PROC_PIDT_SHORTBSDINFO)` 取 ppid；多根递归插入；program 值相等复用同一 Arc（内存去重）；单 pid 失败跳过不致命。
- **argv 来源**：ES 事件不含 argv——EXEC 事件由内核 `es_exec_arg` 提取；seeding 由 `KERN_PROCARGS2` 补查。解释型进程的脚本身份关键证据。
- **已知洞**（Santa 同款）：parent 不在树里的 fork → 子树缺失；seeding + 强制 FORK/EXEC/EXIT 订阅缓解。

### 4.1 Agent 注解语义（实测修正案⑥，对齐 Santa Originator）

初版规划为「executable 位于 Agent 目录下即打标；exec 换成目录外程序则摘除」。实测 Hermes 暴露两个归因 gap 后修正为：

1. **打标条件**：executable **或任一 argv 元素**命中 Agent 目录前缀。理由：venv python 的真实 executable 解析到目录外（如 uv 管理的 `~/.local/share/uv/python/.../python3.11`），脚本路径在 argv 里（`venv/bin/hermes`）。
2. **EXEC 传播**：新 executable 命中则按新值打标（支持换绑到另一个 Agent），**否则继承旧节点注解**。理由：agent 派生的进程 exec 到目录外程序（如 curl）仍是 agent 行为，摘除会丢归因——Santa Originator 即如此传播。

效果（Hermes 冒烟）：归因分布从 actor_process 421 / target_path 509 改善为 **actor_process 1137 / both 612 / target_path 0**，并捕获 agent 对目录外文件的 791 次操作。

## 5. 作用域过滤：双向匹配

系统级订阅 + 用户态过滤，发 AgentEvent 的条件（OR）：

1. **actor_process**：事件进程（actor）节点带 Agent 注解（§4.1）——覆盖 Agent 对**任意路径**的行为（如读用户文档、写 /dev/tty）；
2. **target_path**：事件目标路径位于任一 Agent 工作目录下——覆盖**其他进程**触碰 Agent 数据（如用户手删 config）。

每条 AgentEvent 记录 `agent.match: actor_process | target_path | both`，可审计归因依据。过滤在写线程做廉价预检（注解 O(1) 查询 + realpath 规范化前缀匹配，防 `/foo/bar` 误伤 `/foo/bar2`），不命中即丢弃。

## 6. AgentEvent 归一化 Schema（`agent-event/1.0`）

设计原则：Actor–Operation–Target 模型，一条事件一眼可读**谁（actor）在什么时候（time）对什么目标（target）执行了什么操作（operation），附带哪些基本数据（detail/context），产生什么行为结果（outcome）**。信封（sensor/schema/seq）与语义载荷分离，`schema_version` 显式版本化。AgentEvent 是 ES 事件经处理/编译编码后的产物，核心在 verb 归一化（§6.2）。

### 6.1 顶层结构

```json
{
  "schema_version": "agent-event/1.0",
  "event_id": "01K5...(ULID)",
  "time": "2026-09-01T06:00:00.123456789Z",
  "seq": {"global": 19116, "local": 42},
  "sensor": {"name": "es-agent-sensor", "version": "0.1.0", "pid": 1234},

  "agent": {"id": "hermes", "workdir": "/Users/nanzhi/.hermes",
            "match": "actor_process | target_path | both"},

  "actor": {
    "pid": 71634, "pidversion": 2715978, "ppid": 71583,
    "program": {"executable": ".../venv/bin/python3",
                "argv": ["python3", "-m", "tui_gateway.entry"],
                "signing_id": "...", "team_id": "...", "cdhash": "...",
                "is_platform_binary": false},
    "cred": {"ruid": 501, "euid": 501, "rgid": 20, "egid": 20, "auid": 501},
    "origin": {"agent_root_pid": 71583,
               "agent_root_executable": ".../bin/hermes",
               "ancestry": [".../bin/hermes", ".../venv/bin/python3"],
               "depth_from_agent_root": 1},
    "responsible_pid": 71583
  },

  "operation": {"category": "file | process | ipc | net",
                "verb": "write",
                "outcome": "completed | snapshot",
                "es_event": {"type": "ES_EVENT_TYPE_NOTIFY_CLOSE",
                             "id": 12, "message_version": 10}},

  "target": {"kind": "file | process | socket | endpoint", "...": "多态，见 §6.3"},

  "context": {"agent_workdir_hit": "/Users/nanzhi/.hermes/config.yaml",
              "mach_time": 4731552479268, "thread_id": 6011254}
}
```

`actor.origin.ancestry` 给出到 Agent 根的归因链（沿父链回溯，截断到 Agent 根），一眼看清行为链是谁拉起来的。`actor.program` 字段命名与 eslogger JSON 对齐，降低下游对齐成本。

### 6.2 verb 归一化表

| ES NOTIFY 事件 | verb | 归一化逻辑 |
|---|---|---|
| EXEC | `exec` | target=新进程；argv 由 `es_exec_arg` 提取 |
| FORK | `spawn` | target=子进程 |
| EXIT | `exit` | 含 `exit_stat`；target.program 由进程树节点回填 |
| CREATE | `create` | `existed` 区分新建/覆盖已存在 |
| OPEN | `open` | fflag → `access: read\|write\|readwrite` + 原始 fflag 保留 |
| CLOSE | `write` | **仅 modified=true 才发**（未修改 close 降噪丢弃），等价「写完成」 |
| RENAME | `move` | path → dst_path；`existed` 表示 dst 覆盖 |
| UNLINK | `delete` | `is_dir` 区分 file/rmdir |
| LINK / CLONE | `link` / `clone` | src → dst |
| SETATTRLIST | `setattr` | `attrs`：attrgroup 位图 → 属性名列表 |
| TRUNCATE | `truncate` | — |
| UIPC_BIND | `ipc_bind` | target=socket 路径 + mode |
| UIPC_CONNECT | `ipc_connect` | 已订阅；**实测不投递**（§7） |
| net-poller diff | `net_connect` / `net_close` | `outcome: "snapshot"`，区别于实时 `completed`；无 ES 序号（seq=0） |

### 6.3 target 多态载荷（`kind` 判别，serde internally-tagged）

- `file`：`path` `dst_path` `access` `fflag` `existed` `is_dir` `attrs` `stat{ino,mode,size,uid,gid,mtime}`
- `process`：`pid` `pidversion` `program`（同 actor.program）`exit_stat`
- `socket`：`domain` `path` `socket_type` `protocol` `mode` `peer_process`
- `endpoint`：`proto(tcp4|tcp6|udp4|udp6)` `local` `remote`（五元组）

Option 字段一律 `skip_serializing_if=None`，事件行紧凑。

## 7. ESF 盲区与补偿

- **XPC/mach IPC**：ESF 不暴露，不可观测（如实声明，无解）。
- **TCP/UDP**：ESF 无事件。**net-poller 补偿**：按 `--net-poll-interval`（默认 2s，`0` 关闭）对 Agent 进程树成员枚举 socket（libproc `PROC_PIDFDSOCKETINFO`），按五元组 diff 出 `net_connect`/`net_close`，`outcome: "snapshot"`。基线轮之前的存量连接不出事件；短于轮询间隔的连接可能漏。
- **NOTIFY_UIPC_CONNECT 实测不投递**（macOS 26.5.2，同进程/跨进程 connect 均无事件；SDK 注释显示该事件带 AUTH 缓存键，qzhddr 生产实践也只订阅 AUTH 变体）。NOTIFY-only 约束下 IPC 感知 = `UIPC_BIND`（server 侧）；订阅保留 UIPC_CONNECT，未来 OS 恢复投递则零成本接入。

## 8. 性能与稳定性设计

- C shim 回调只做字段拷贝；Rust 侧有界 channel（8192）+ 单写线程；满则丢 + `dropped` 计数（实测零丢弃）。
- `--mute-path` 内核级 target path 前缀静音（默认不静音，保感知完整）。
- `--stats-interval` 周期吞吐/丢弃统计到 stderr；事件 JSON 只走 stdout/文件，诊断日志（tracing，无 ANSI）走 stderr。
- 优雅停机：drain channel → flush → 最终统计。**实测修正案⑤**：签名 ES client 进程受 macOS 反篡改保护，SIGINT/SIGTERM/SIGUSR1 均无效（root 发送亦然，信号掩码为 0），仅 SIGKILL 有效——故提供 `--duration N` 有界运行；JSONL 写即 flush，SIGKILL 无数据丢失风险。

## 9. 工程结构与交付物

```
quest-mvp-lab/es-agent-sensor-mvp/
├── Cargo.toml / Cargo.lock     # 单 crate（lib+bin），lock 提交（可复现构建）
├── build.rs                    # cc 编译 shim（-fblocks），链 EndpointSecurity/bsm/proc
├── csrc/es_shim.{c,h}          # ES 字段提取 + libproc socket 枚举（proc_pidfdinfo）
├── src/
│   ├── main.rs                 # CLI 入口、root 检查、--duration、优雅停机
│   ├── cli.rs                  # --agent/--output/--extra-events/--mute-path/
│   │                           #   --net-poll-interval/--stats-interval/--log-level/--duration
│   ├── ffi.rs                  # repr(C) 镜像 + 安全包装（Client RAII/trampoline/list_sockets）
│   ├── backend.rs              # EsBackend：RealEs / MockEs
│   ├── decode.rs               # C 结构 → (verb, category, target, TreeOp) 纯函数
│   ├── schema.rs               # agent-event/1.0 serde 类型 + RFC3339 纳秒格式化
│   ├── proctree.rs             # Santa 移植进程树（§4）
│   ├── matcher.rs              # AgentSet：realpath、前缀匹配、双向 combine
│   ├── pipeline.rs             # 有界 channel + Assembler + 写线程
│   ├── sink.rs / metrics.rs / netpoll.rs / sysproc.rs / error.rs
├── examples/lsocks.rs          # 诊断工具：枚举指定 pid 的 socket
├── packaging/                  # Info.plist 模板 + entitlements（仅 es.client）
├── scripts/build.sh            # release → .app → profile → codesign → verify
├── scripts/e2e.sh              # 端到端验收（假 Agent 场景 + JSONL 断言）
├── scripts/fake_agent.c        # e2e 行为序列发生器（现场编译，ad-hoc 签名）
└── README.md                   # 背景/决策/schema/构建运行/实测结论/已知限制
```

依赖：`clap(derive)` `serde` `serde_json` `tracing(-subscriber)` `thiserror` `anyhow` `libc` `ulid` `signal-hook` `crossbeam-channel`；build-dep `cc`。无 ES crate。

**运行前提**：`.app` 壳 + `embedded.provisionprofile` + `com.apple.developer.endpoint-security.client` entitlement + root + 完全磁盘访问（FDA 按签名身份记账，重编不重复授权）。

## 10. 实测修正案汇总（规划 → 交付的偏差）

| # | 规划假设 | 实测结论（macOS 26.5.2） | 处置 |
|---|---|---|---|
| ① | 订阅集含 RMDIR（15 事件） | ES 无 NOTIFY_RMDIR，rmdir 归并 UNLINK | 默认集 14 事件；`delete` verb 按 stat mode 区分 dir |
| ② | ES 事件枚举值稳定 | CLONE/READDIR/UIPC_* 在旧 SDK 与 26.5 SDK 间偏移 2 | 以编译期 SDK 为准；`ffi.rs` 钉桩测试守护 |
| ③ | audit_token 布局「pid 在 val[0]」 | `val[0]=auid…val[5]=pid val[6]=asid val[7]=pidversion`（探针实测） | sysproc 修正 + 数值断言测试 |
| ④ | NOTIFY_UIPC_CONNECT 可用 | 不投递（AUTH 缓存键事件） | 订阅保留、文档如实标注；e2e 断言不含 ipc_connect |
| ⑤ | SIGINT/SIGTERM 优雅停机 | ES client 进程信号免疫，仅 SIGKILL | 新增 `--duration`；JSONL 写即 flush |
| ⑥ | exec 到目录外程序摘除注解 | Hermes venv python/curl 场景归因丢失 | 注解语义改对齐 Santa Originator（argv 命中 + exec 继承，§4.1） |
| ⑦ | proc_pidinfo 可枚举 socket | fd 级 flavor 必须走 `proc_pidfdinfo`（flavor 值 3 碰撞 PROC_PIDT_BSDINFO，静默返回 136B 错结构） | shim 修正 + lsocks 诊断工具 |
| ⑧ | 拷贝 /bin/bash 当假 Agent | 平台二进制约束，拷离系统卷即 SIGKILL | e2e 改现场编译 ad-hoc 签名 C 程序 |

## 11. 验收结果（已达成）

- `cargo test`：**86 个单测全绿**（无需 root）；
- `cargo llvm-cov`（排除 root-only 的 `main.rs`）：**行覆盖 96.71%**；
- `cargo clippy --all-targets -- -D warnings`、`cargo fmt --check`：通过；
- `sudo ./scripts/e2e.sh`：PASS——115 条事件覆盖 exec/spawn/exit/create/open/write/move/delete/truncate/ipc_bind/net_connect/net_close 全部 verb；双向归因、origin 回溯、CLOSE→write 归一化、exec argv、net 快照语义断言全过；`dropped=0`；
- Hermes 真实冒烟（35s）：matched **1749** 条（received 54055，**drop=0 errors=0**），JSONL 全量可解析，exec 全带 argv，actor_process 全带 origin；捕获目录外文件操作 791 次与本机代理 TCP connect/close；
- 合规：企业标识 grep 零命中；签名产物与 profile 副本测完即删；`Cargo.lock` 提交；`target/`、`*.app`、`*.provisionprofile` gitignore。

## 12. 已知限制

- XPC/mach IPC 不可经 ESF 观测（仅 UIPC）。
- NOTIFY_UIPC_CONNECT 实测不投递（修正案④）。
- TCP/UDP 为秒级轮询快照，短命连接可能漏；存量连接不出基线事件。
- parent 不在树内的 fork 子树缺失（Santa 同款已知洞）。
- 路径匹配基于 realpath 规范化前缀（`/tmp` → `/private/tmp`）。
- 需要 root + entitlement 签名 + FDA；进程信号免疫，外部停止只能 SIGKILL 或 `--duration`。
