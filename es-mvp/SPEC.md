# ES-MVP：基于 es_invert_muting 的目录级 AUTH_OPEN 管控最小验证

> **日期**：2026-08-10
>
> **目标**：用最小代码验证「静音反转（inversion）+ AUTH_OPEN」组合在真机（SIP 开启、非开发者模式）上的可行性与事件减量效果，为"从全盘订阅转向目录级精准监听"的架构方案提供实证。
>
> **形态**：单目录、单源文件的命令行工具，外包一层最小 .app bundle（原因见 [§4](#4-为什么外包一层-app-bundle)）。

## 目录

- [1. 功能规格](#1-功能规格)
- [2. ES 调用序列（顺序有官方约束，不可乱）](#2-es-调用序列顺序有官方约束不可乱)
- [3. MIME 判定方式](#3-mime-判定方式)
- [4. 为什么外包一层 .app bundle](#4-为什么外包一层-app-bundle)
- [5. 签名与运行前置条件](#5-签名与运行前置条件)
- [6. 验证用例（DoD）](#6-验证用例dod)
- [7. 风险登记](#7-风险登记)
- [8. 实测结论](#8-实测结论)
- [9. 目录结构](#9-目录结构)

## 1. 功能规格

命令行：

```bash
sudo ./objc/ESMvp.app/Contents/MacOS/esmvp [--watch <目录>]... [--cache] [--verbose] [--stats-interval <秒>]
```

- `--cache`（缓存验证）：ALLOW 响应带 `cache=true` 启用内核授权缓存，DENY 永不缓存；语义验证脚本 `sudo ./scripts/test-cache.sh`（在对应版本目录下执行）。
- **不指定 `--watch`**：inversion 生效且 target-mute 规则为空 → 内核抑制**全部** AUTH_OPEN（等价"静音所有 open"）。被 mute 的 AUTH 事件 = 自动放行，整机 open 行为不受影响，本进程也几乎收不到事件（用统计计数证明"真的没收到"）。
- **指定一个或多个 `--watch <目录>`**：仅当 AUTH_OPEN 的目标文件位于这些目录下时，事件才投递到本进程，由用户态裁决；目录之外的一切 open 在内核侧直接放行、不上送。
- **裁决规则**：按 MIME type（标准判定，见 [§3](#3-mime-判定方式)）——`image/png` / `image/jpeg` → **DENY**（`open()` 返回 `EACCES`）；其余类型/无扩展名/目录本身 → ALLOW。
- **观测**：每条被裁决的事件输出一行日志（path、MIME、决策）；每 10 秒输出一次统计（received / allowed / denied / respond_error），用于对比 mute-all 模式下的"零事件"。

## 2. ES 调用序列（顺序有官方约束，不可乱）

依据 SDK `ESClient.h` 中 `es_invert_muting` 的注释原文：

> Consider calling `es_unmute_all_target_paths` before inverting target path muting. **Make sure the client has no auth subscriptions before doing so.**

即 unmute-all / invert 必须发生在任何 AUTH 订阅之前。因此序列定为：

```text
1. es_new_client
2. es_unmute_all_target_paths        // 清默认 mute set（否则反转后默认集被"选中"）
3. es_invert_muting(ES_MUTE_INVERSION_TYPE_TARGET_PATH)
4. es_muting_inverted(...) == ES_MUTE_INVERTED  // 自检，不过则报错退出
5. 对每个 --watch 目录：realpath 规范化 + 结尾补 "/" → es_mute_path(ES_MUTE_PATH_TYPE_TARGET_PREFIX)
6. es_subscribe(AUTH_OPEN)           // 最后才订阅 AUTH
7. 进入 runloop，统计线程每 10s 打一次点
```

handler 内只做 O(1) 工作：取目标路径 → MIME 判定 → `es_respond_flags_result` 应答（flags=0 即 DENY，放行回传事件原 `fflag`）。**不做任何文件 I/O、不打大日志、不持锁**——MVP 顺便验证"handler 零阻塞"范式。MVP 默认 `cache=false` 保证每次行为可观测，`--cache` 时 ALLOW 写入内核缓存。

## 3. MIME 判定方式

扩展名 → `UTType`（UniformTypeIdentifiers.framework）→ `preferredMIMEType`：

- 优点：不读文件内容，无 TCC/FDA 依赖（监控 `~/Documents` 也不需要额外授权），O(1) 纯内存操作，符合 handler 零阻塞要求。
- 已知局限：`evil.png` 改名 `evil.txt` 可绕过——MVP 接受此局限；生产方案应接内容嗅探（届时需评估文件读取权限）。

## 4. 为什么外包一层 .app bundle

`com.apple.developer.endpoint-security.client` 是托管 entitlement，exec 时由 amfid 校验 **embedded provisioning profile**；profile 只能放在 bundle 的 `Contents/embedded.provisionprofile`，裸 mach-o 无法内嵌。因此最小可行形态 = 无 UI 的 .app 壳（`Info.plist` + `Contents/MacOS/esmvp` + `Contents/embedded.provisionprofile`），仍从终端以 `sudo` 命令行方式运行，开发就是一个 `objc/src/main.m` + `objc/scripts/build.sh`。

## 5. 签名与运行前置条件

运行环境要求：macOS 13.0+（inversion API 引入版本）、SIP 保持开启、无需开发者模式。

**使用者需在 Apple Developer 后台完成（详细步骤见根 [README.md](README.md)「开始前你需要准备」）：**

1. 新建 App ID（如 `com.example.esmvp`），勾选 **Endpoint Security** capability（需先获得 Apple 对该托管 entitlement 的审批）。
2. 新建 **Developer ID** 类型 Provisioning Profile 并放入各版本的 `packaging/` 目录。构建命令：`./scripts/build.sh`（或 `make package`，在各版本目录下执行）。
3. **TCC 完全磁盘访问授权（每个 bundle ID 首次运行前必做）**：Developer ID 分发的 ES client，`es_new_client` 会返回 `ERR_NOT_PERMITTED(rc=4)` 直到用户在 **系统设置 → 隐私与安全性 → 完全磁盘访问权限** 中添加对应 App 并打开开关。授权按代码签名身份（bundle ID + 证书）记账，重新编译/签名不影响已授权状态。企业部署的等价物 = MDM 下发 PPPC（SystemPolicyAllFiles）配置。

## 6. 验证用例（DoD）

| # | 场景 | 预期 |
|---|------|------|
| 1 | 无 `--watch` 运行，`sudo cat` 任意文件、打开任意 App | 一切正常打开；10s 统计显示 received≈0（证明内核全静音生效） |
| 2 | `--watch ~/es-test`，目录内放 `a.png` / `b.jpg` / `c.txt` | `cat a.png` → `Operation not permitted`；`cat c.txt` → 正常输出；日志含 DENY/ALLOW 记录 |
| 3 | 同次运行中访问 watch 目录**外**的 png（如 `~/es-other/x.png`） | 正常打开，且**无任何事件日志**（证明目录外事件未上送） |
| 4 | watch 目录内子目录 `~/es-test/sub/d.png` | 被拦截（前缀语义生效） |
| 5 | 符号链接路径传入 `--watch`（如经 `/tmp` 链接） | realpath 规范化后规则仍生效（规则入库前规范化） |
| 6 | 连续高频 open（`find` 遍历大目录） | watch 目录外不产生事件；进程 CPU≈0；无 `ENDPOINTSECURITY Code 2` |
| 7 | 杀掉进程后再 open watch 目录内 png | 正常打开（client 消失 = 授权默认放行，验证无残留状态） |

## 7. 风险登记

| # | 风险 | 应对 |
|---|------|------|
| R1 | ~~AUTH 订阅与 inversion 共存是否成立~~ **✅ 已实证成立**（见 [§8](#8-实测结论)） | — |
| R2 | inversion 清默认 mute set 的理论副作用（默认集防死锁，AUTH_OPEN 一般不在其中，风险低） | 用 `es_muted_paths_events` 打印默认集留档；异常时恢复默认集重试 |
| R3 | mute 路径前缀是字符串级匹配，`/foo/bar` 误伤 `/foo/bar2` | 规则统一以 `/` 结尾 + realpath 规范化（§2 第 5 步） |
| R4 | 硬链接绕过：敏感文件的另一硬链接入口在 watch 目录外 | MVP 不处理，写入威胁模型备注 |

## 8. 实测结论

环境：2026-08-10，macOS 26.5.2 / SIP 开启 / 无开发者模式 / Developer ID 正式签名。全部 DoD 用例通过，关键结论：

1. **R1 成立**：`es_unmute_all_target_paths` → `es_invert_muting(TARGET_PATH)` → `es_mute_path` → 最后 `es_subscribe(AUTH_OPEN)` 的序列下，**AUTH_OPEN + target inversion 组合可用**。`--watch /Users` 运行时投递事件全部位于 `/Users` 下，目录外零上送；无 `--watch` 时 `received=0`（全静音生效，系统行为不受影响）。
2. **目录外拦截为零**：watch 目录外的 png 正常打开且无任何事件到达——"只收指定目录事件"的反转语义实证成立，事件量与整机负载解耦。
3. **踩坑记录（对正式实现有直接影响）**：
   - AUTH_OPEN 必须用 `es_respond_flags_result` 应答（flags=0 即 DENY，放行回传 `fflag`）；误用 `es_respond_auth_result` 会导致响应全部失败 → deadline 超期 → 被 ES SIGKILL（实测复现 `Namespace ENDPOINTSECURITY, Code 2`）。
   - Developer ID 形态 ES client 必须先获 **TCC 完全磁盘访问授权**，否则 `es_new_client` 返回 rc=4 `ERR_NOT_PERMITTED`（rc=5 = 非 root，rc=3 = 缺 entitlement，三者不同）。
   - 裸 Mach-O 无法内嵌 provisionprofile，最小可行形态 = 无 UI .app 壳内跑 CLI；托管 entitlement 由 amfid 在 exec 时校验 embedded profile。
4. **噪声实证**：`--watch /Users` 几秒内的 35 条 ALLOW 事件（Spotlight、IM 日志、浏览器 IndexedDB、输入法缓存……）全部是与管控无关的背景 open——"全盘订阅 + 用户态丢弃"架构成本来源的直观样本。
5. **内核授权缓存（`cache=true`）语义实测**（`test-cache.sh` Case A–F，全通过）：
   - 无缓存基线：3 次 open = 3 条事件；开启后 **3 次新进程 `cat` 同一文件只产生 1 条事件**——缓存**跨进程实例**生效，短命进程高频 open 场景事件量可降一个量级；
   - **修改文件 → 缓存条目自动失效**：写 open 后的下一次读 open 重新上送（WWDC20 声称的失效语义属实），管控正确性边界成立；
   - **缓存键含可执行文件维度**（Case F）：`cat` 建立的 ALLOW 缓存对 `python3` 不生效——ALLOW 不会跨不同可执行文件外溢，缓存白名单的安全口径比文档暗示的更严格；
   - **DENY 不缓存**：每次拦截都上送、都被拒，拦截面可观测性不受缓存影响。
   - 结论：AUTH_OPEN 缓存键 ≈（可执行文件 × 目标文件 × open flags），修改即失效，ALLOW 可缓存、DENY 不缓存——内核授权缓存是 AUTH 面安全且高效的减量杠杆。

## 9. 目录结构

```text
es-mvp/
├── SPEC.md       # 本文件
├── README.md     # 项目手册（快速开始 / 前置准备 / FAQ）
├── objc/         # ObjC 版（src/main.m + packaging/ + scripts/ + Makefile）
├── rs/           # Rust 版（完全自持的 Cargo 工程，见其 README.md）
└── swift/        # Swift 版（完全自持的 SwiftPM 工程，见其 README.md）
```
