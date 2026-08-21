# SPEC — 进程级 es_invert_muting + bundleId 策略匹配

> 只监听 YAML 中列出的进程（按 bundleId），其 `AUTH_OPEN` 一律 DENY，其余进程默认 ALLOW。
> 真机结论：**可行**，但必须配一个非反转发现客户端。核验日期 2026-08-21。

## 1. 背景与目标

Endpoint Security（ESF）默认订阅即接收全系统事件。目录级（target-path 反转）可以只收指定目录的
`AUTH_OPEN`；本 MVP 验证**进程级**（process 反转）能否只收指定进程的事件，并用 **bundleId**
做策略匹配。

目标：

1. 本地 YAML 配置 `bundleIds` 列表，纳入管控的进程打开 **PDF 文件** 的 `AUTH_OPEN` 一律 DENY。
2. 未命中列表的进程默认 ALLOW，目标外进程/事件在内核侧静音、零上送、零开销。
3. 详细日志 + 统计，可确证运行状态。

## 2. 应用场景

DLP / EDR 的「渠道管控」与「软件管控」属于 actor 维度：只关心特定进程干了什么。进程反转让
监控客户端**天然只收目标进程事件**，无需在用户态按 pid 过滤全系统事件流——既省 CPU，也天然防漏。
bundleId 是进程最稳定的跨会话身份（不同于会复用的 pid），是策略匹配的自然键。

## 3. 技术原理：进程反转静默

`es_invert_muting(client, ES_MUTE_INVERSION_TYPE_PROCESS)`（macOS 13.0+）把 mute 判定翻转。
头文件原文（`ESClient.h`）：「Inverting muting can be used to create a client that monitors a
specific process(es) or set of directories.」反转后 `es_mute_process(tok)` 的语义**翻转**为
「开始 watch（投递）」，`es_unmute_process(tok)` 为「停止 watch（抑制）」——与直觉相反，须隔离。

三条已核验语义（详见 skill `es-invert-muting-semantics`）：

- **默认静音集陷阱只影响 PATH/TARGET_PATH 反转**，进程反转不受影响。进程反转是最干净的一种，
  反转前无需 `es_unmute_all_paths`。
- **OR 组合、反转先发生**：进程反转 + 路径 mute 组合时，路径 mute 仍压制已选中进程。
- **watch 集随进程退出自动清理**（process mute 随退出移除）。

## 4. bundleId 解析：signing_id == bundleId

策略匹配键用 `es_process_t.signing_id`（代码签名标识）。真机实测（spike3 抓 exec 事件）：

```
path=/Applications/WeChat.app/Contents/MacOS/WeChat
signing_id=com.tencent.xinWeChat
team_id=5A4RE8SF68
```

`signing_id` 等于 App 的 `CFBundleIdentifier`（WeChat → `com.tencent.xinWeChat`）。它随 exec
事件免费携带（无需读 Info.plist / SecStaticCode），是进程级策略的标准键，Santa 亦用
signing_id + team_id 做策略。未签名进程 `signing_id` 为空，不命中策略（默认 ALLOW）。

## 5. 核心架构约束：反转客户端是「盲」的

进程反转后，未选中进程一律静音 → 收不到它们的 `AUTH_EXEC` → 无法发现新启动的目标进程。
这是鸡生蛋问题。Santa 的解法（本 MVP 对齐）是拆两个客户端：

```
┌─────────────────┐        es_mute_process(tok)         ┌─────────────────┐
│   发现客户端      │ ────────────────────────────────→ │   监控客户端      │
│  非反转          │        (bundleId 命中策略的 exec)     │  进程反转         │
│  订阅 AUTH_EXEC  │                                     │  订阅 AUTH_OPEN  │
└────────┬────────┘                                     └────────┬────────┘
         │ 全系统 exec                                         │ 仅被 watch 进程的 open
         ▼                                                     ▼
   bundleId 命中 → watch + ALLOW exec                 PDF 一律 DENY，非 PDF 放行
```

发现客户端（非反转）看到全系统 exec；命中策略即取 `es_event_exec_t.target.audit_token`
（exec 后身份，pidversion 已更新）`es_mute_process` 到监控客户端。非命中 exec 走「立即 ALLOW +
写内核缓存」快速路径，不落日志。**exec 本身永远 ALLOW**（策略只管控 AUTH_OPEN）。

## 6. 初始化序列（官方顺序约束）

```
new_monitor_client → invert_muting(PROCESS) → ensure_inverted
  → new_discovery_client → subscribe(AUTH_EXEC) → subscribe(AUTH_OPEN)
```

顺序理由：

- **监控客户端必须先建并反转**：发现客户端的 handler 要往监控客户端 `es_mute_process`。
- **反转必须先于订阅**：`ESClient.h` 要求反转前无 auth 订阅。
- **订阅必须最后**：`es_new_client` 成功会自动清共享缓存；订阅前的时间窗事件可能被其他
  client 缓存（P12）。

## 7. 关键实测：es_mute_process 需要精确 audit token

进程反转的「已运行进程 watch」问题：能否在启动时枚举已在运行的目标进程并 mute？

**实测否定**。用 `proc_pidinfo(PROC_PIDTBSDINFO)` + `getaudit_addr()` 构造的 token
（pid/euid/egid/ruid/rgid/auid/asid 全正确，仅 pidversion 置 0），`es_mute_process` 返回
`ES_RETURN_ERROR`，事件不投递。

根因：`es_mute_process` 校验完整 `audit_token_t`（8×u32，含 pidversion），而 pidversion
**无公开 API 可从 pid 反查**（`proc_info.h` 无此字段，`kinfo_proc` 公开结构已剔除 `e_au`）。
因此精确 token **只能从 ES 事件取得**。

**结论**：目标进程须在本程序启动后 launch 才能被 watch。这是进程反转的固有边界，Santa 亦如此。

## 8. 实测结论（真机，macOS 26.5.2，SIP 开启）

| 语义 | 实测结论 |
| ---- | -------- |
| signing_id == bundleId | ✅ WeChat `signing_id=com.tencent.xinWeChat` |
| 进程反转 + AUTH 订阅 | ✅ invert 后 mute=watch，事件正常投递 |
| 命中 → 打开 PDF DENY | ✅ Preview 打开 `.pdf` 被 DENY（`open decision=DENY path=…/test.pdf`） |
| 命中 → 非 PDF ALLOW | ✅ WeChat 正常启动，1255 次 open 全放行、零 DENY |
| 未命中 → 默认 ALLOW | ✅ 零 DENY |
| 目标外进程 open | ✅ 零上送（非管控进程打开零日志） |
| 已运行进程 watch | ❌ 无法（§7） |

关键日志摘录（控制 Preview，命中 PDF）：

```
open  bundle=com.apple.Preview decision=DENY path=/private/tmp/esproc-e2e/esproc-test.pdf
```

控制 WeChat（非 PDF 全放行）：

```
stats ... controlled=1 open_received=1255 denied=0 allowed=1255 ...
```

## 9. AUTH_OPEN 缓存是共享缓存 footgun（真机踩坑）

`es_respond_flags_result` 的 `cache` 参数语义微妙（`ESClient.h` @note 明示）：
缓存的是 `authorized_flags`，后续同名文件命中缓存时，仅当「本次 flags ⊆ 缓存 flags」才放行，
否则**误判为 DENY**。因此要么传 `UINT32_MAX`，要么干脆不缓存。

真机实测：即使传 `UINT32_MAX`，`cache=true` 仍会让 WeChat 启动即崩（open 只投递 1 条后进程退出，
且不经过我的 handler，`denied` 计数看不到）。改 `cache=false` 后 WeChat 正常启动（1255 次 open 全放行）。

**结论**：本 MVP 对 `AUTH_OPEN` 响应一律 `cache=false`（逐条应答）。handler 是 O(路径长) 字符串
判断 + 一次内核应答，成本可接受；正确与简单优先于微优化。

## 10. 测试矩阵

| 层 | 用例 | 结果 |
| -- | ---- | ---- |
| 单元（无 root） | YAML 解析、策略匹配、PDF 判定（大小写/后缀边界）、初始化序列、反转失败终止、exec watch/忽略、open PDF-DENY/非PDF-ALLOW 应答映射、未签名进程不命中 | 27/27 ✅ |
| e2e（sudo） | Case 1 控制 Preview → PDF DENY + TXT ALLOW；Case 2 控制 WeChat → 非 PDF 全放行正常启动；Case 3 未命中 → 默认 ALLOW | ALL PASS ✅ |

## 11. 代码结构

```
Sources/ESProcessMvpCore/
  Cli.swift       # swift-argument-parser：位置参数 configPath（其余参数已删）
  Config.swift    # YAML 加载（Yams）→ PolicyConfig
  Policy.swift    # Policy：bundleId 集合匹配 + PDF 判定（denyOpen(bundleId:path:)）
  Backend.swift   # EsBackend 协议 + RealEs（双客户端）+ MockEs；ExecEvent/OpenEvent 携带 bundleId
  App.swift       # 编排：初始化序列 + 事件处理 + 统计 + 信号
  Stats.swift     # 锁保护计数
  Log.swift       # 结构化控制台日志
```

设计要点：

- **后端可替换**：编排层只依赖 `EsBackend` 协议，`MockEs` 回放事件支撑无 root 测试。
- **策略单一来源**：`Policy.denyOpen(bundleId:path:)`（管控 + PDF）在监控端决定 DENY；
  发现端用 `isControlled(bundleId)` 决定 watch。
- **应答前先 watch**：命中 exec 先 `es_mute_process` 再 `es_respond_auth_result`，缩小
  open 丢失窗口；命中事件永不缓存（每次 launch 都要重新 mute 新 pid）。
- **flags 类与 auth 类应答分开**：`AUTH_OPEN` 用 `es_respond_flags_result`（DENY=flags 0，
  ALLOW=UINT32_MAX，cache=false），`AUTH_EXEC` 用 `es_respond_auth_result`（永远 ALLOW）。
