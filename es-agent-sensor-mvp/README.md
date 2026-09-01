# es-agent-sensor-mvp

macOS Endpoint Security（**仅 NOTIFY 事件**）AI Agent 行为感知传感器（Rust）。
命令行指定一个或多个 Agent 工作目录，传感器系统级订阅 ES 事件，经进程树归因 +
路径双向匹配，输出归一化的 **AgentEvent JSONL**，详细记录「谁在什么时候对什么
目标执行了什么操作、附带什么数据、产生什么行为」。

## 背景

AI Agent（如 Hermes Agent，工作目录 `~/.hermes`）在本机执行大量自主行为：派生
子进程、读写配置与缓存、SQLite 事务、Unix Socket IPC、对外网络连接。要感知这些
行为，需要一个内核级数据源——Endpoint Security Framework（ESF）。

本 MVP 回答三个问题：

1. **订哪些事件**：事件太多卡顿、太少感知缺失。反推 Agent 行为面后审慎取舍（见下）。
2. **如何归因**：系统级事件流中哪些属于「Agent 的行为」——进程树（移植 Google
   Santa ProcessTree 设计）+ Agent 目录路径双向匹配。
3. **输出什么形态**：ES 原始事件语义粗糙（如 CLOSE/OPEN/UNLINK），归一化为
   Actor–Operation–Target 模型的 AgentEvent。

## 关键决策

### 事件订阅集（14 个 NOTIFY + 3 个可选）

| Agent 行为 | 订阅事件 | 取舍 |
|---|---|---|
| 派生进程 | `EXEC` `FORK` `EXIT` | 进程树骨架；EXIT 驱动延迟回收 |
| 写文件 | `CLOSE`(modified) `CREATE` `TRUNCATE` | **不订 `WRITE`**（每次 write(2) 一条，SQLite 场景爆炸）；`CLOSE.modified=true` 是去重后的写完成信号 |
| 读文件 | `OPEN` | fflag 给出 read/write/readwrite 意图 |
| 原子写/移动 | `RENAME` | tmp+rename 模式取证关键 |
| 删除 | `UNLINK` | macOS ES 无 RMDIR 事件，rmdir 归并 UNLINK，按 stat mode 区分 |
| 硬链/克隆 | `LINK` `CLONE` | 低频低价 |
| 属性变更 | `SETATTRLIST` | chmod/xattr 等 |
| IPC | `UIPC_BIND` `UIPC_CONNECT` | **ESF 不暴露 XPC/mach IPC**，Unix Socket 是可观测 IPC 的全部 |

明确不订：`WRITE`（极高频）、`READDIR`/`CHDIR`/`FSGETPATH`（高频低值）、`SIGNAL`、
`PROC_CHECK`、全部 `AUTH_*`（NOTIFY-only，无 deadline 压力）。`--extra-events
write,readdir,signal` 可显式加订。

### 归因：双向匹配

- **actor_process**：事件进程属于 Agent 进程树（进程树的 `agent_id` 注解：
  executable **或任一 argv 元素**位于 Agent 目录下即打标——解释型脚本场景
  如 venv python 的真实可执行文件解析到目录外，脚本路径在 argv 里；fork
  继承；exec 时新 executable 命中则换绑，否则继承旧注解，对齐 Santa
  Originator——agent 派生的 curl 仍是 agent 行为）。覆盖 Agent 对**任意
  路径**的操作。
- **target_path**：事件目标路径位于 Agent 目录下。覆盖**其他进程**触碰 Agent
  数据（如用户手删配置）。
- 每条事件记录 `agent.match: actor_process | target_path | both`。

### 进程树（移植 Santa ProcessTree）

- 键 `(pid, pidversion)`，天然解决 pid 复用；平铺 `HashMap` + 单向父链
  `Arc<Process>`，不维护子链。
- FORK 共享父 `Arc<Program>`（零拷贝）；EXEC 新建节点、旧 key 进延迟回收堆
  （~5s grace）；EXIT 延迟回收；事件去重 `(mach_time, kind, actor, other)` 有界
  LRU（16384），只丢精确重复。
- 启动 seeding：libproc 枚举现存进程，`task_info(TASK_AUDIT_TOKEN)` 取
  pidversion/cred，`KERN_PROCARGS2` 取 argv，多根回溯插入；单 pid 失败跳过。
- ES 事件不含 argv —— EXEC 事件的 argv 由 `es_exec_arg` 提取（内核提供），
  seeding 由 `KERN_PROCARGS2` 补查。

### 网络连接补偿（ESF 盲区）

ESF 无 TCP/UDP 事件。net-poller 按 `--net-poll-interval`（默认 2s，0 关闭）对
Agent 进程树成员枚举 socket（libproc `PROC_PIDFDSOCKETINFO`），diff 出
`net_connect`/`net_close` 快照事件（`outcome: "snapshot"`）。**轮询快照非实时，
短生命周期连接可能漏。**

### 性能架构

- C shim（`csrc/es_shim.c`）在 ES 回调里只做字段拷贝到扁平 C 结构体，立刻返回；
  所有策略逻辑在 Rust 侧。
- 有界 channel（8192）背压：满则丢 + 计 `dropped`；单写线程组装 + 序列化 + 落盘。
- 多 Agent 目录共用一个 es_client。
- `--mute-path` 可追加内核级 target-path 静音（默认不静音，保感知完整）。

## AgentEvent Schema（`agent-event/1.0`）

每行一条 JSONL。信封（sensor/schema/seq）与语义载荷分离：

```json
{
  "schema_version": "agent-event/1.0",
  "event_id": "01K5...",
  "time": "2026-09-01T06:00:00.123456789Z",
  "seq": {"global": 19116, "local": 42},
  "sensor": {"name": "es-agent-sensor", "version": "0.1.0", "pid": 1234},
  "agent": {"id": "hermes", "workdir": "/Users/nanzhi/.hermes", "match": "both"},
  "actor": {
    "pid": 71634, "pidversion": 2715978, "ppid": 71583,
    "program": {"executable": ".../venv/bin/python3", "argv": ["python3", "-m", "tui.entry"],
                "signing_id": "...", "team_id": "...", "is_platform_binary": false},
    "cred": {"ruid": 501, "euid": 501, "rgid": 20, "egid": 20, "auid": 501},
    "origin": {"agent_root_pid": 71583, "agent_root_executable": ".../bin/hermes",
               "ancestry": [".../bin/hermes", ".../venv/bin/python3"],
               "depth_from_agent_root": 1},
    "responsible_pid": 71583
  },
  "operation": {"category": "file", "verb": "write", "outcome": "completed",
                "es_event": {"type": "ES_EVENT_TYPE_NOTIFY_CLOSE", "id": 12, "message_version": 10}},
  "target": {"kind": "file", "path": "/Users/nanzhi/.hermes/config.yaml",
             "stat": {"ino": 1, "mode": 33188, "size": 512, "uid": 501, "gid": 20, "mtime": 0}},
  "context": {"agent_workdir_hit": "/Users/nanzhi/.hermes/config.yaml",
              "mach_time": 4731552479268, "thread_id": 6011254}
}
```

**verb 归一化**（AgentEvent 是 ES 事件经处理/编译编码后的产物）：

| ES NOTIFY | verb | 说明 |
|---|---|---|
| EXEC | `exec` | target=新进程（含 argv） |
| FORK | `spawn` | target=子进程 |
| EXIT | `exit` | 含 exit_stat |
| CREATE | `create` | `existed` 区分新建/覆盖 |
| OPEN | `open` | `access: read\|write\|readwrite` + 原始 fflag |
| CLOSE | `write` | **仅 modified=true 才发**（未修改 close 降噪丢弃） |
| RENAME | `move` | path → dst_path |
| UNLINK | `delete` | `is_dir` 区分 file/rmdir |
| LINK / CLONE | `link` / `clone` | src → dst |
| SETATTRLIST | `setattr` | `attrs` 属性名列表 |
| TRUNCATE | `truncate` | |
| UIPC_BIND | `ipc_bind` | Unix socket 路径（server 侧）；UIPC_CONNECT 已订阅但实测不投递（见已知限制） |
| net-poller | `net_connect` / `net_close` | `outcome: "snapshot"`，五元组 |

`target.kind`：`file`（path/dst_path/stat/access/existed/is_dir/attrs/fflag）·
`process`（pid/pidversion/program/exit_stat）· `socket`（domain/path/peer_process）·
`endpoint`（proto/local/remote）。

## 构建

```bash
cargo build            # 开发构建（单测不需要 root）
cargo test             # 单元测试
cargo llvm-cov --summary-only   # 覆盖率
```

### 签名打包（真机运行必需）

ES client 必须：`.app` 壳 + `embedded.provisionprofile` +
`com.apple.developer.endpoint-security.client` entitlement，且以 root 运行并被
授予完全磁盘访问（FDA）。

```bash
# 1. 在 Apple 开发者后台为 com.nanzhipro.esagentsensor 创建含 Endpoint Security
#    权限的 provisioning profile，放到 packaging/es-agent-sensor.provisionprofile
#    （profile 属敏感资产，.gitignore 已排除，绝不提交）
# 2. 构建 + 签名 + 校验
./scripts/build.sh     # BUNDLE_ID / IDENTITY / PROFILE 均可用环境变量覆盖
# 3. 首次运行前：系统设置 → 隐私与安全性 → 完全磁盘访问权限，添加 ESAgentSensor.app
```

## 运行

```bash
# 监控 Hermes Agent（输出到 stdout）
sudo ./ESAgentSensor.app/Contents/MacOS/es-agent-sensor --agent hermes=$HOME/.hermes

# 多 Agent + 输出到文件 + 内核静音系统目录
sudo ./ESAgentSensor.app/Contents/MacOS/es-agent-sensor \
  --agent hermes=$HOME/.hermes --agent claude=$HOME/.claude \
  --output /tmp/agent-events.jsonl --mute-path /System/

# 调试：加订高频事件、提高日志级别
sudo ... --extra-events write,readdir --log-level debug
```

停机：`SIGINT`/`SIGTERM` 优雅停机（drain channel、flush、输出最终统计到 stderr）。

## 端到端验收

```bash
./scripts/build.sh && sudo ./scripts/e2e.sh
```

e2e 用临时目录充当假 Agent（拷入 /bin/bash 当 agent 可执行文件），执行派生进程 /
增删改 rename / Unix socket / TCP 连接 / 外部触碰目录内文件，然后对 JSONL 断言
verb 覆盖、双向归因、origin 回溯、CLOSE→write 归一化等。`--keep-app` 保留签名产物。

## 实测结论

macOS 26.5.2（M 系列芯片，SDK 26.5）实测：

- **单元测试**：86 个全绿；`cargo llvm-cov` 行覆盖 **96.71%**（排除 root-only 的
  `main.rs`；剩余缺口为真 ES 成功路径等 root 依赖代码）。
- **e2e（假 Agent）**：`sudo ./scripts/e2e.sh` PASS——115 条事件覆盖
  exec/spawn/exit/create/open/write/move/delete/truncate/ipc_bind/
  net_connect/net_close 全部 verb，双向归因、origin 回溯、CLOSE→write
  归一化、exec argv、net 快照语义断言全过；`dropped=0`。
- **Hermes 真实冒烟**（`--agent hermes=$HOME/.hermes`，35s）：matched **1749**
  条（received 54055，filtered 为系统其他进程事件，**dropped=0 errors=0**）。
  归因分布 actor_process 1137 / both 612 / target_path 0；捕获到 hermes 对
  目录外文件的 791 次操作（如 node 写 /dev/ttys*）及对本机代理
  `127.0.0.1:7897` 的 TCP connect/close。
- **吞吐**：系统级 14 事件订阅约 1500~6500 事件/秒（随系统负载波动），
  有界 channel 背压下零丢弃；CPU 占用可忽略。

### 实测踩坑记录（重要）

1. **audit_token 布局**：`val[0]=auid val[1]=euid val[2]=egid val[3]=ruid
   val[4]=rgid val[5]=pid val[6]=asid val[7]=pidversion`（探针实测验证；
   手册式记忆极易错位）。
2. **proc_pidfdinfo vs proc_pidinfo**：fd 级 flavor（如 PROC_PIDFDSOCKETINFO）
   必须走 `proc_pidfdinfo(pid, fd, flavor, ...)`；误用 `proc_pidinfo` 会因
   flavor 值碰撞（3 = PROC_PIDT_BSDINFO）静默返回错误结构（136B vs 792B）。
3. **ES 事件枚举值随 SDK 漂移**：CLONE/READDIR/UIPC_* 在旧 SDK 与 26.5 SDK
   之间偏移了 2。以编译期 SDK 为准并在 `ffi.rs` 钉桩测试守护。
4. **NOTIFY_UIPC_CONNECT 不投递**（26.5.2 实测，同进程与跨进程 connect 均无
   事件；SDK 注释显示该事件带 AUTH 缓存键，生产实践均订阅 AUTH 变体）。
   NOTIFY-only 约束下 IPC 感知 = `UIPC_BIND`（server 侧）+ 订阅保留
   UIPC_CONNECT（未来 OS 若恢复投递零成本接入）。
5. **签名 ES client 进程信号免疫**：SIGINT/SIGTERM/SIGUSR1 均被忽略（root
   发送也无效，信号掩码为 0），仅 SIGKILL 有效——macOS 对带 ES entitlement
   进程的反篡改保护。有界运行用 `--duration`；JSONL 写即 flush，SIGKILL
   无数据丢失风险。
6. **拷贝系统平台二进制（/bin/bash）到临时目录执行会被 SIGKILL**（平台
   二进制约束）；e2e 改用现场编译的 ad-hoc 签名 C 程序。
7. **解释型 Agent 的归因**：venv python 的真实 executable 解析到 agent 目录
   外（如 uv 管理的 python）。注解语义（对齐 Santa Originator）：executable
   或任一 argv 元素命中 agent 目录即打标；FORK 继承；EXEC 时新 executable
   命中则换绑，否则继承旧注解（agent exec curl 仍是 agent 行为）。

## 已知限制

- **XPC/mach IPC 不可经 ESF 观测**（仅 UIPC）。
- **NOTIFY_UIPC_CONNECT 实测不投递**（见踩坑记录 4）。
- **TCP/UDP 无 ES 事件**，net-poller 为秒级快照，短命连接可能漏；基线轮
  之前的存量连接不出事件（仅 diff）。
- parent 不在树内的 fork 事件子树缺失（Santa 同款已知洞，seeding + 强制
  FORK/EXEC/EXIT 订阅缓解）。
- 路径匹配基于 realpath 规范化前缀（`/tmp` → `/private/tmp`）。
- 需要 root + entitlement 签名 + FDA。
- 信号免疫（见踩坑记录 5）：外部停止只能 SIGKILL 或 `--duration`。

## 工程说明

- unsafe 仅存在于 `src/ffi.rs` 与 `src/sysproc.rs`（每处有 SAFETY 注释）；
  上层经 `EsBackend`/`ProcSource` 注入抽象，单测零 root 依赖。
- 事件 JSON 只写 stdout/文件；诊断日志（tracing，无 ANSI）写 stderr。
- `Cargo.lock` 已提交（可复现构建）；`target/`、`*.app`、`*.provisionprofile`
  已 gitignore。
