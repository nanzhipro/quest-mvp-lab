# es-procattr-mvp

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform: macOS 13+](https://img.shields.io/badge/platform-macOS%2013%2B-lightgrey.svg)](https://www.apple.com/macos/)
[![Language: Swift](https://img.shields.io/badge/language-Swift-orange.svg)](https://swift.org)
[![Dependencies: stdlib only](https://img.shields.io/badge/dependencies-stdlib%20only-green.svg)](#零依赖)

macOS Endpoint Security **进程归因（进程基因 / 进程链 / 行为链）** 的最小可行验证：
以 AI Agent（Hermes）为标的，实时回答「谁拉起了哪个进程、进程链如何、生命周期如何、干了什么」。

> 能否用 ESF 拿到进程的「基因」（身份 + 血缘 + 责任链），并实时重建一个 AI Agent 的
> 进程树与文件行为链？
> 真机实测结论：**可以**（macOS 26.5.2，SIP 开启，单 NOTIFY 客户端，无任何阻断）。

## 特性

- 🧬 **进程基因**：每次进程执行捕获 `(pid, pidversion)`、可执行路径、**argv**、`signing_id`、
  `team_id`、`ppid`、`responsible_audit_token`（谁负责拉起）——对齐 Santa `ProcessTree` 语义
- 🌳 **进程链**：祖先链（lineage）一路从 `launchd` 走到叶子进程；进程树只存父链、渲染时重建子链
- 🔄 **生命周期**：`NOTIFY_EXEC / FORK / EXIT` 三事件驱动（exec = 同 pid 新 pidversion 换镜像）
- 📁 **行为链**：`NOTIFY_OPEN/CLOSE/CREATE/RENAME/UNLINK/WRITE` 归因到关注子树内的进程
- 🎯 **argv 归因**：解释型进程（python/node）的「脚本身份」在 argv 而非可执行路径，种子经
  `KERN_PROCARGS2`、实时经 exec 后补查——这是 AI Agent 归因的关键
- 🗂️ **零依赖**：仅 Foundation + EndpointSecurity + libproc，离线可构建
- 📊 **可观测**：结构化控制台日志 + 可选 JSONL 事件流 + 周期进程树快照

## 目录

- [背景](#背景)
- [核心设计](#核心设计)
- [开始前你需要准备](#开始前你需要准备)
- [构建与运行](#构建与运行)
- [验证](#验证)
- [实测结论](#实测结论)
- [已知限制](#已知限制)
- [FAQ](#faq)
- [License](#license)

## 背景

EDR 行为链的核心是「进程归因」：每个文件/进程事件都要能回答**是谁干的、谁派它来的、它的链条是什么**。
AI Agent（如 Hermes）是典型的「解释型 + 多子进程」负载——它自己是一组 python/node 进程，
运行时高频 fork/exec 出 `bash`/`git`/`python` 等工具进程并读写文件。要观测它，不能只看可执行路径，
必须把 `argv`（脚本身份）纳入基因。

本 MVP 用单一 `es_new_client` 订阅全系统 NOTIFY 事件（无 AUTH、无阻断、无 deadline 压力），
用户态维护一棵对齐 Santa 语义的进程树（`(pid, pidversion)` 键），按配置的 `roots` 判定「关注子树」，
只归因子树内的事件。

## 核心设计

### 1. 进程基因（ProcessGene）

一次进程执行的身份快照，字段全部来自 `es_process_t`（种子阶段来自 libproc/mach）：

| 字段 | 来源 | 语义 |
| ---- | ---- | ---- |
| `pid`, `pidversion` | audit_token | 唯一标识（exec 递增 pidversion） |
| `path` | `executable.path` | 可执行文件（解释器）路径 |
| `arguments` | `KERN_PROCARGS2` | **脚本/命令身份**（ES 事件不含 argv） |
| `signing_id`, `team_id` | 代码签名 | bundleId / 团队 |
| `ppid`, `parent_audit_token` | 进程结构 | 直接父进程 |
| `responsible_audit_token` | 进程结构 | **责任进程**（谁负责拉起，存活于 reparent） |

### 2. 进程树（对齐 Santa ProcessTree）

- 键是 `(pid, pidversion)`，不是裸 pid（防 pid 复用误判）。
- **只存父链不存子链**；`lineage`（= Santa `RootSlice`）从叶子一路向上取祖先链。
- `fork` = 新 pid、同镜像；`exec` = 同 pid、新 pidversion、换镜像、沿用父链；`exit` = 墓碑保留。

### 3. 双阶段采集

| 阶段 | 机制 | 职责 |
| ---- | ---- | ---- |
| 种子（Backfill） | libproc 枚举 + `task_info(TASK_AUDIT_TOKEN)` 取 pidversion + `KERN_PROCARGS2` 取 argv | 发现**已在运行**的 Hermes 及其祖先/后代 |
| 实时 | 单 ES client 订阅 9 个 NOTIFY 事件 | 追踪新 fork/exec/exit 与文件行为 |

## 开始前你需要准备

本项目依赖 Apple 的**托管权限**（managed entitlement），无法即下即用，请按序准备：

1. **Apple Developer Program 付费账号**。
2. **Endpoint Security entitlement 已获批**的 App ID（仓库内为占位符 `com.example.esmvp`）。
3. **签名物料**：该 App ID 的 Developer ID provisioning profile（`.provisionprofile`）+
   本机钥匙串的 Developer ID Application 证书。
4. **macOS 13.0+**，SIP 保持开启。

> [!IMPORTANT]
> `com.apple.developer.endpoint-security.client` 由 amfid 在进程启动时校验 embedded
> provisioning profile，没有 profile 将无法创建 ES client。本仓库不包含任何 profile / 证书。

## 构建与运行

```bash
make test         # XCTest 单元测试（无需 root，30 用例）
make package      # release 构建 + 签名打包 ESProcattr.app
make e2e          # 真机 DoD 用例验证（需 root）
```

直接运行（首次把三个环境变量指向你自己的签名物料）：

```bash
BUNDLE_ID=com.example.esmvp \
PROFILE=/path/to/your.provisionprofile \
IDENTITY="Developer ID Application: Your Name (TEAMID)" \
./scripts/build.sh

sudo ./ESProcattr.app/Contents/MacOS/es-procattr ./config.example.json --output procattr.jsonl
```

**首次运行前还需一步**：系统设置 → 隐私与安全性 → **完全磁盘访问权限** → 添加构建出的 App
并打开开关（Developer ID 形态的 ES client 必需；授权按签名身份记账，重编译无需重复）。

## 验证

```bash
make e2e          # DoD：种子发现 → 实时 exec 归因 → 文件行为链归因
```

单元测试 `swift test`（30 用例，无需 root）覆盖：`(pid,pidversion)` 键与 pid 复用、
fork/exec/exit 语义、scope 传播、lineage、树渲染、JSON 配置解析、argv 根匹配、事件归因计数。

## 实测结论

真机（macOS 26.5.2，SIP 开启，root + FDA + Developer ID）验证，完整数据见
[SPEC.md §8](SPEC.md#8-实测结论)，要点：

| 语义 | 实测结论 |
| ---- | -------- |
| Hermes 进程发现（argv 归因） | ✅ 种子经 `KERN_PROCARGS2` 命中 7 个根（node/gateway/hermes，路径均为解析后的解释器） |
| 进程链（lineage） | ✅ `launchd→Code→zsh→python3.11(hermes)→node→gateway→bash→git` 完整正确 |
| 责任进程（responsible） | ✅ 新进程 `resp=96612`（VS Code，Hermes 的启动者） |
| exec 基因 | ✅ `pid`/`pidversion`/`argv`/`signing_id`/`parent` 全量捕获 |
| 行为链 | ✅ `create→write→close→rename→unlink` 归因到正确进程（mkdir/cp/mv/rm） |
| 事件量 | ✅ 80 秒观察窗口 1040 条 in-scope 事件，全系统事件正确过滤 |

## 已知限制

1. **种子节点无 signing_id / responsible**：libproc 枚举拿不到代码签名信息（Santa 用 csops 补，
   MVP 省略）；这些字段在进程 re-exec 后补齐。
2. **argv 匹配的子串误命中**：Hermes 派生的 bash 其 `-c` 命令串含 Hermes 环境变量（如
   `AI_AGENT=hermes-agent`），会额外命中 pathContains 成为「根」——scope 仍正确（它确是 Hermes 后代），
   仅根列表略噪。
3. **fork 子进程 argv 为空**：fork 继承父镜像但 argv 不在 fork 事件中，待其 exec 时补齐
   （Santa 在 HandleFork 显式继承父 argv，MVP 未做，属等价的小差异）。
4. **墓碑不回收**：exit 节点保留不删（MVP 运行窗口内可忽略；Santa 有 5 秒 grace 回收）。
5. **单客户端无事件去重**：Santa 因多客户端需要 `EventKey` 去重；本 MVP 单客户端无需。

## FAQ

- **`es_new_client 失败 rc=5`**：需要 root，用 `sudo` 运行。
- **rc=4**：缺少完全磁盘访问授权（见[构建与运行](#构建与运行)末节）。
- **rc=3**：签名缺少 ES entitlement——检查 embedded profile 与 App ID 是否匹配。
- **启动即 `Killed: 9`（退出码 137）**：ES entitlement 未获批——触发一次运行后在系统设置点「允许」。
- **种子 roots=0**：`pathContains` 匹配的是可执行路径 **或 argv**；若目标进程是解释型且其
  argv 不含该子串，请改用其实际 argv 中的脚本路径作 `pathContains`。
- **`sign=` 为空**：种子节点无签名信息（见[已知限制](#已知限制)），re-exec 后补齐。

## License

[MIT](LICENSE) © es-procattr-mvp contributors
