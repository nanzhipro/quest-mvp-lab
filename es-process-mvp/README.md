# es-process-mvp

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform: macOS 13+](https://img.shields.io/badge/platform-macOS%2013%2B-lightgrey.svg)](https://www.apple.com/macos/)
[![Language: Swift](https://img.shields.io/badge/language-Swift-orange.svg)](https://swift.org)

macOS Endpoint Security **进程级静音反转（`es_invert_muting(PROCESS)`）** 的最小可行验证：
用 **bundleId 策略匹配**，只监听 YAML 中列出的进程，其余进程在内核侧全量静音。

> 能否只管控指定进程的文件打开事件，其余进程零干扰？
> 真机实测结论：**可以**（macOS 13+，SIP 开启，无需开发者模式）。策略由本地 YAML 配置，
> bundleId 命中即「纳入管控」，其打开 **PDF 文件** 的 `AUTH_OPEN` 一律 DENY；其余默认 ALLOW。

## 特性

- 🎯 **进程级精准监听**：反转静音后，只有被 `es_mute_process` 选中的进程事件会上送，
  其余进程在内核侧静音、零开销、零日志
- 🔍 **双客户端架构**（对齐 Santa）：非反转发现客户端订 `AUTH_EXEC`，进程反转监控客户端订 `AUTH_OPEN`
- 🏷️ **bundleId 策略匹配**：按进程代码签名标识（= bundleId）判定，见 `config.example.yaml`
- 🚫 **纳入管控即 DENY PDF**：bundleId 命中 → 该进程打开 **PDF** 一律 DENY；非 PDF / 未命中 → 默认 ALLOW
- 📊 **可观测**：结构化控制台日志 + 周期统计，无 ANSI 转义，可直接 grep

## 目录

- [策略配置](#策略配置)
- [核心机制](#核心机制)
- [开始前你需要准备](#开始前你需要准备)
- [构建与运行](#构建与运行)
- [验证](#验证)
- [实测结论](#实测结论)
- [已知限制](#已知限制)
- [FAQ](#faq)
- [License](#license)

## 策略配置

策略文件为 YAML，`bundleIds` 列出纳入管控的进程：

```yaml
# config.example.yaml
bundleIds:
  - com.tencent.xinWeChat    # WeChat 的 AUTH_OPEN 一律 DENY
  - com.example.other        # 可追加多个
```

- bundleId = 进程的**代码签名标识**（`es_process_t.signing_id`，实测等于 App 的
  `CFBundleIdentifier`，如 WeChat → `com.tencent.xinWeChat`）。
- 命中列表 → 该进程打开 **PDF 文件** 时 `AUTH_OPEN` 被 DENY；非 PDF、或未命中 → 默认 ALLOW。
- PDF 判定为扩展名匹配（`.pdf`，大小写不敏感），不读文件内容。
- 空列表 → 不管控任何进程，全部默认 ALLOW。

## 核心机制

进程反转客户端「只投递在 mute 集内的进程事件」，反转后 `es_mute_process(tok)` 语义**翻转**为
「开始 watch 该进程」。但反转客户端收不到 `AUTH_EXEC`，无法自行发现新启动的进程（鸡生蛋问题），
因此必须拆两个客户端：

| 客户端 | 反转 | 订阅 | 职责 |
| ------ | ---- | ---- | ---- |
| 发现客户端 | 否 | `AUTH_EXEC` | 看到全系统 exec；按 bundleId 命中策略即 `es_mute_process` 到监控客户端 |
| 监控客户端 | 是（process） | `AUTH_OPEN` | 只收到被 watch 进程的打开事件；PDF 一律 DENY，非 PDF 放行 |

初始化序列有官方顺序约束：先建监控客户端并反转 → 再建发现客户端（其 handler 依赖监控客户端已就位）→
最后订阅。完整设计见 [SPEC.md](SPEC.md)。

## 开始前你需要准备

本项目依赖 Apple 的**托管权限**（managed entitlement），无法即下即用，请按序准备：

1. **Apple Developer Program 付费账号**。
2. **Endpoint Security entitlement 已获批**的 App ID（需你自行申请并配置，仓库内为占位符 `com.example.esmvp`）。
3. **签名物料**：
   - 下载该 App ID 的 **Developer ID** provisioning profile（`.provisionprofile`）；
   - 本机钥匙串需有 **Developer ID Application** 证书（与 profile 同一 Team）。
4. **macOS 13.0+**，SIP 保持开启。

> [!IMPORTANT]
> `com.apple.developer.endpoint-security.client` 由 amfid 在进程启动时校验 embedded
> provisioning profile，没有 profile 将无法创建 ES client。本仓库不包含任何
> profile / 证书——它们属于你的开发者账号，已被 `.gitignore` 排除。

## 签名物料替换说明

本项目**不含任何公司级签名物料**——bundle ID、证书身份、provisioning profile 均为占位符，
构建前请替换为你自己的：

| 项 | 仓库占位值 | 替换方式 |
| -- | ---------- | -------- |
| Bundle ID | `com.example.esmvp` | 环境变量 `BUNDLE_ID`（需是你自己、已获批 Endpoint Security entitlement 的 App ID） |
| 签名身份 | 自动探测本机 `Developer ID Application` 证书 | 环境变量 `IDENTITY`（`Developer ID Application: Your Name (TEAMID)`） |
| Provisioning Profile | `./packaging/esmvp.provisionprofile` | 环境变量 `PROFILE`（你自己的 `.provisionprofile` 路径） |

首次构建把这三个环境变量指向你自己的签名物料即可；profile 文件与签名后的 `.app` 产物
均被 `.gitignore` 排除，不会随仓库分发。

## 构建与运行

```bash
make test         # XCTest 单元测试（无需 root）
make package      # release 构建 + 签名打包 ESProcessMvp.app
make e2e          # 真机 DoD 用例验证（sudo）
```

直接运行：

```bash
sudo ./ESProcessMvp.app/Contents/MacOS/es-process-mvp ./config.example.yaml
```

可变参数用环境变量覆盖：

```bash
BUNDLE_ID=com.example.esmvp \
PROFILE=/path/to/your.provisionprofile \
IDENTITY="Developer ID Application: Your Name (TEAMID)" \
./scripts/build.sh
```

**首次运行前还需一步**：系统设置 → 隐私与安全性 → **完全磁盘访问权限** → 添加构建出的
App 并打开开关（Developer ID 形态的 ES client 必需；授权按签名身份记账，之后重新编译无需重复）。

## 验证

```bash
make e2e          # DoD 用例：bundleId 命中 → PDF open DENY / 非 PDF & 未命中 → 默认 ALLOW
```

单元测试 `swift test`（27 个用例，无需 root）覆盖：YAML 解析、策略匹配、PDF 判定、
初始化序列、exec/open 的 DENY/ALLOW 应答映射、非管控快速放行等。

## 实测结论

真机（macOS 26.5.2，SIP 开启）验证，完整数据见 [SPEC.md §8](SPEC.md#8-实测结论)，要点：

| 语义 | 实测结论 |
| ---- | -------- |
| signing_id == bundleId | ✅ WeChat 的 `signing_id=com.tencent.xinWeChat`（= CFBundleIdentifier） |
| 进程反转 + AUTH 订阅 | ✅ invert 后 `es_mute_process` = watch，正常投递 |
| 命中 → 打开 PDF DENY | ✅ Preview 打开 `.pdf` 被 DENY（`open decision=DENY path=…/test.pdf`） |
| 命中 → 非 PDF ALLOW | ✅ WeChat 正常启动，1255 次 open 全放行、零 DENY |
| 未命中 → 默认 ALLOW | ✅ 零 DENY |
| 目标外进程事件 | 零上送（非管控进程 open 零日志） |
| 已运行进程的 watch | ❌ 无法（见[已知限制](#已知限制)） |

## 已知限制

1. **纳入管控的进程须在本程序启动后 launch 才能被 watch**。进程反转客户端「盲」于全系统 exec，
   而 `es_mute_process` 需要精确的 `audit_token_t`（含 pidversion），该 token **只能从 ES 事件取得**，
   无公开 API 可从 pid 反查（实测构造 token 的 `es_mute_process` 返回 `ES_RETURN_ERROR`）。
   这是进程反转的固有边界，Santa 亦如此。
2. **发现客户端需要看全系统 exec**。这是发现目标进程的必要代价，但非管控 exec 走
   「立即 ALLOW + 写内核缓存」快速路径，用户态开销极小且不落日志。
3. **bundleId 匹配依赖代码签名**：未签名进程 `signing_id` 为空，不会被命中（默认 ALLOW）。
4. **PDF 判定是扩展名匹配**：`.pdf` 后缀（大小写不敏感），不读文件内容；改名可绕过（MVP 接受）。
5. **AUTH_OPEN 响应不写内核缓存**：flags 缓存是共享缓存且语义微妙，真机实测缓存会让 WeChat
   启动即崩（见 SPEC §9），故全部逐条应答，换取正确与简单。

## FAQ

- **`es_new_client 失败 rc=5`**：需要 root，用 `sudo` 运行。
- **rc=4**：缺少完全磁盘访问授权（见[构建与运行](#构建与运行)末节）。
- **rc=3**：签名缺少 ES entitlement——检查 embedded profile 与 App ID、capability 是否匹配。
- **启动即 `Killed: 9`（退出码 137）**：ES entitlement 未获批——触发一次运行后在
  系统设置 → 隐私与安全性 → 安全性点「允许」。
- **改了 bundle ID 后 rc=4**：FDA 授权按签名身份记账，换 ID 需重新授权一次。
- **杀掉进程后拦截失效**：属预期——client 消失即授权默认放行，无任何残留状态。
- **配置了 bundleId 但进程没被拦**：确认该进程是在 daemon 启动**之后**启动的（见[已知限制](#已知限制)），
  且 `signing_id` 与配置值完全一致（可用 `codesign -dvv <binary>` 查）。

## License

[MIT](LICENSE) © es-process-mvp contributors
