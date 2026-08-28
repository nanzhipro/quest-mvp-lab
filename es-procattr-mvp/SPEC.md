# SPEC — ESF 进程归因：AI Agent 进程基因 / 进程链 / 行为链

> 用 ESF 拿到进程的「基因」，实时重建 AI Agent（Hermes）的进程树与文件行为链。
> 真机结论：**可行**。核验日期 2026-08-29。

## 1. 背景与目标

EDR 行为链的第一步是「进程归因」：每个事件都要能回答**谁干的、谁派它来的、链条是什么**。
AI Agent（Hermes）是典型的「解释型 + 多子进程」负载：

- 自身是一组 python/node 进程（node 跑 UI、python 跑 gateway 与 agent 主体）；
- 运行时高频 fork/exec 出 `bash`/`git`/`python`/`node` 等工具进程，并读写文件。

目标：

1. 识别 Hermes 的进程集群（含**已在运行**的进程）。
2. 重建进程树：父子链、祖先链（谁拉起了谁）、生命周期（exec/fork/exit）。
3. 归因文件行为链：谁 create/write/rename/unlink 了什么。
4. 输出可观测的实时日志 + JSONL。

## 2. 应用场景

- **AI Agent 审计**：追踪一个 Agent 会话到底拉起/执行了哪些进程、读写/删除了哪些文件。
- **EDR 归因底座**：进程树 + 责任进程（responsible process）是策略评估、事件日志、告警的通用前置。
- **解释型进程身份**：python/node 的「可执行路径」是解释器二进制，真正的「身份」在 argv。

## 3. 关键洞察一：解释型进程的「身份」在 argv

真机实测 Hermes 的进程（`ps` + `lsof` 核验）：

| 进程 | 可执行路径（proc_pidpath 解析后） | argv 中的身份 |
| ---- | -------------------------------- | ------------- |
| UI | `/Users/nanzhi/.hermes/node/bin/node` | `node --expose-gc …/ui-tui/dist/entry.js` |
| gateway | `…/uv/python/cpython-3.11.15…/bin/python3.11`（venv 符号链接解析后） | `python3 -m tui_gateway.entry` |
| agent | 同上 python3.11 | `python3 …/venv/bin/hermes --tui` |

`"hermes-agent"` 这个子串**不在可执行路径里**（node 在 `.hermes/node/`，python 在 uv 缓存目录），
而在 **argv 的脚本/模块路径**（`…/hermes-agent/ui-tui/dist/entry.js`、`…/hermes-agent/venv/bin/hermes`）里。

**结论**：进程基因必须包含 argv，否则无法按「产品身份」归因解释型进程。获取途径（对齐 Santa
`ProcessArgumentsForPID`）：`sysctl KERN_PROCARGS2`。

## 4. 关键洞察二：进程树对齐 Santa ProcessTree

参考 `~/github/santa/Source/common/processtree/`，本 MVP 采用其核心语义：

- **键是 `(pid, pidversion)`**（`struct Pid`），不是裸 pid——pid 复用 + exec 递增 pidversion，
  两元组才是「一次进程执行」的唯一标识。
- **只存父链不存子链**（查问题永远「往上查」）；渲染子链时瞬时重建。
- **三事件语义**：
  - `fork` → 新 pid、同镜像（子继承父 program/cred）。
  - `exec` → 同 pid、新 pidversion、换镜像，**父链沿用 exec 前节点的父**（`HandleExec` 继承 `p.parent_`）。
  - `exit` → 立墓碑（Santa 有 5 秒 grace 回收，本 MVP 保留不删）。
- **Backfill 顺序**：先有爸后有儿，从根（ppid=0）自顶向下插入。

### 种子 pidversion 的唯一权威来源

libproc 无公开 API 反查 pidversion（es-process-mvp SPEC §7 已证 `es_mute_process` 需精确 token），
但 Santa 用 `task_name_for_pid` + `task_info(TASK_AUDIT_TOKEN)` 拿到**完整 audit token（含 pidversion）**。
本 MVP 照做——否则 `(pid, pidversion)` 键在种子阶段无法建立，文件事件无法按精确 id 归因。

## 5. 架构

```
┌─────────────── 种子（Backfill，订阅前）────────────────┐
│ libproc 枚举 pid → proc_pidpath / task_info(audit_token) │
│              / KERN_PROCARGS2 → 建 (pid,pidversion) 树    │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│  单一 es_new_client（NOTIFY-only，无 AUTH 无阻断）        │
│  订阅：EXEC/FORK/EXIT + OPEN/CLOSE/CREATE/RENAME/UNLINK/  │
│        WRITE（9 事件）                                    │
│  handler 内同步解码 → EsEvent（零持有，NOTIFY 无应答义务） │
└───────────────────────────┬─────────────────────────────┘
                            ▼
        ProcessTree.handleExec/Fork/Exit（(pid,pidversion) 键）
                            ▼
        scope 判定（根命中 or 父链在 scope）→ 归因 + 日志 + JSONL
```

- **scope** = 进程命中 roots（路径或 argv 子串 / signingId）或祖先链经过根。
- **全系统进程都入库**（每节点轻量 struct，MVP 窗口内内存可忽略），仅 in-scope 事件落日志。

## 6. 运行条件（profile 安置与获取）

`es_new_client` 需要三条腿（与 es-mvp / es-process-mvp 相同）：

1. **root**（`sudo`）。
2. **完全磁盘访问（FDA）**：否则 `es_process_t->executable->path` 为 NULL，无法取路径。
3. **embedded provisioning profile** 含 `com.apple.developer.endpoint-security.client`（托管权限，
   Apple 审批），amfid 在 exec 时校验。

本机 profile 安置在 `~/.esmvp-local/esmvp_profile.provisionprofile`（App ID `com.example.esmvp` 对应的
已获批托管权限，Team ID 略，有效期 2044），`scripts/build.sh` 的 PROFILE 探测顺序为
`./packaging/` → `~/.esmvp-local/`。FDA 与 ES 审批按「签名身份 + App ID」记账——沿用同一 bundle ID
+ 证书重编译无需重复授权。

## 7. 踩坑记录

| 坑 | 现象 | 根因 | 修复 |
| -- | ---- | ---- | ---- |
| `mach_task_self_` 误当函数 | SIGBUS（exit 138），task_name_for_pid 前崩溃 | `mach_task_self_` 是**全局变量**（`mach_task_self()` 宏读它），不是函数；`@_silgen_name` 声明成函数导致跳转到数据地址 | 声明为 `private var mach_task_self_: UInt32` |
| pathContains 匹配不到 Hermes | 种子 roots=0 | 可执行路径是解析后的解释器（node / uv python），"hermes-agent" 在 argv | 根匹配同时查 path **和** argv；种子与实时 exec 都补查 `KERN_PROCARGS2` |
| 后台 + sudo 无 TTY | "a terminal is required to read the password" | 后台进程无 tty，sudo 无法读密码（`sudo -S` 管道被平台拦截） | 前台 `sudo bash start.sh`，脚本内用 `( daemon & )` 子 shell 脱离控制终端 |
| `nohup` 无法 detach | "can't detach from console" | `do shell script` 无控制终端，nohup 的 ioctl 失败 | 改用 `( cmd < /dev/null > log 2>&1 & )` 子 shell 脱离 |

## 8. 实测结论（真机，macOS 26.5.2，SIP 开启）

80 秒观察窗口，Hermes 为标的，`pathContains: "hermes-agent"`：

| 语义 | 实测结论 |
| ---- | -------- |
| 种子发现 | ✅ 7 个根（两个会话的 node/gateway/hermes），均经 argv 命中 |
| 进程链 | ✅ `launchd→Code→Code Helper→zsh→python3.11(hermes)→node→gateway→bash→<tool>` 完整 |
| 责任进程 | ✅ 新进程 `resp=96612`（VS Code，Hermes 启动者）；`signing_id` 如 `com.apple.git`、`com.apple.bash` |
| exec 基因 | ✅ `pid`/`pidversion`/`argv`/`signing_id`/`parent`/`resp`/`chain` 全量 |
| 行为链 | ✅ 实测 `echo→cat→python3→node→git→mkdir→cp→mv→rm` 的 argv 全捕获；文件 `create→write→close→rename→unlink` 归因正确 |
| 事件量 | ✅ in-scope 1040 条；全系统 exec=193 fork=239 exit=237 open=3955 close=14755 write=6180 正确过滤 |

关键日志摘录（exec，argv 归因 + 完整链）：

```
exec argv=git -C /Users/nanzhi/workspace/quest-mvp-lab log --oneline -1 \
  chain=1:launchd→96612:Code→96685:Code Helper→21956:zsh→22152:python3.11→22214:node→22215:python3.11→649xx:bash→649xx:git \
  parent=649xx pid=649xx pidver=397xxxx ppid=649xx resp=Code sign=com.apple.git
```

行为链摘录（文件生命周期归因）：

```
create pid=64972 proc=mkdir file=/private/tmp/gene-tmp
create pid=64973 proc=cp    file=/private/tmp/gene-tmp/copy.txt
rename pid=64974 proc=mv    file=/private/tmp/gene-tmp/copy.txt dest=/private/tmp/gene-tmp/renamed.txt
unlink pid=64975 proc=rm    file=/private/tmp/gene-tmp/renamed.txt
```

## 9. 测试矩阵

| 层 | 用例 | 结果 |
| -- | ---- | ---- |
| 单元（无 root） | `(pid,pidversion)` 键与 pid 复用、fork/exec/exit 语义、scope 传播、lineage、树渲染、JSON 配置、argv 根匹配、事件归因计数、ProcEnumerator 冒烟 | 30/30 ✅ |
| e2e（root） | 种子发现 7 根 + 实时 exec 归因 + 文件行为链（create/write/rename/unlink） | PASS ✅ |

## 10. 代码结构

```
Sources/ESProcattrCore/
  Token.swift         # audit_token 封装（pid/pidversion 槽位）
  ProcessGene.swift   # Pid(pid,pidversion) + ProcGene（基因）+ EsEvent
  ProcessTree.swift   # (pid,pidversion) 键的进程树（fork/exec/exit/lineage/scope）
  ProcEnumerator.swift# libproc/mach/KERN_PROCARGS2 枚举（种子 + argv 补查）
  Backend.swift       # EsBackend 协议 + RealEs（单 NOTIFY 客户端）+ MockEs
  Config.swift        # JSON 配置（roots + report）
  Cli.swift           # stdlib 手写 CLI 解析
  App.swift           # 编排：种子 → 订阅 → 事件归因 → 快照 → JSONL sink
  Stats.swift / Log.swift
```

设计要点：

- **零第三方依赖**：仅 Foundation + EndpointSecurity + libproc，离线可构建（避开 GitHub 网络抖动）。
- **后端可替换**：编排层依赖 `EsBackend` 协议，`MockEs` 支撑无 root 测试。
- **NOTIFY 零持有**：无 AUTH 事件 → 无应答义务 → handler 内同步提取自有数据即返回（无消息 retain）。
- **JSONL 落盘即写**：`FileHandle` 直写无缓冲，崩溃不丢已写事件。

## 11. 与 Santa 的差异（MVP 简化）

| 维度 | Santa | 本 MVP |
| ---- | ----- | ------ |
| 客户端 | 6 个（AUTH/NOTIFY 分派） | 1 个（NOTIFY-only） |
| 事件去重 | `EventKey` 多客户端去重 | 无（单客户端） |
| 墓碑回收 | 5s grace + ProcessToken 引用计数 | 保留不删 |
| 注解 | Annotator 插件体系（originator 传播） | 无（scope 用布尔） |
| 种子签名信息 | csops（signing_id/team_id/cdhash） | 省略（re-exec 补齐） |
| fork argv | HandleFork 继承父 argv | 省略（exec 补齐） |

## 12. 后续方向

- **argv 去噪**：排除 Hermes 环境变量导致的子串误命中（匹配 argv[0]/argv[1] 而非全 argv）。
- **csops 补种子签名**：种子节点也拿到 signing_id/team_id。
- **`es_new_descendants_client`（macOS 26）**：无需 root/FDA，天然限定观察「自身后代子树」，
  是 AI runtime 自监控的更轻量形态（本 MVP 用 es_new_client 走通的是通用 EDR 形态）。
