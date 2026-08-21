# es-mvp

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform: macOS 13+](https://img.shields.io/badge/platform-macOS%2013%2B-lightgrey.svg)](https://www.apple.com/macos/)
[![Implementations: ObjC | Rust | Swift](https://img.shields.io/badge/implementations-ObjC%20%7C%20Rust%20%7C%20Swift-green.svg)](#三个实现)

macOS Endpoint Security **静音反转（`es_invert_muting`）+ 内核授权缓存**的最小可行验证。

> 能否不订阅全盘事件，只接收指定目录的 `AUTH_OPEN` 并实时裁决？
> 真机实测结论：**可以**（macOS 13+，SIP 开启，无需开发者模式）。

## 特性

- 🎯 **目录级精准监听**：inversion 语义下只接收 watch 目录内的 `AUTH_OPEN`，目录外零事件、零开销
- 🔇 **全静音模式**：不指定目录时全部 `AUTH_OPEN` 在内核侧抑制，进程 `received=0`
- ⚡ **内核授权缓存**：`--cache` 让 ALLOW 结果留在内核，同类事件不再上送用户态
- 🧪 **三语言等价实现**：ObjC / Rust / Swift，同一套验收脚本，行为逐一对齐
- 📊 **可观测**：结构化控制台日志 + 周期统计，无 ANSI 转义，可直接 grep

裁决示例规则：`image/png` / `image/jpeg` 拒绝打开（`open()` 返回 `EACCES`），其余放行。

## 目录

- [三个实现](#三个实现)
- [开始前你需要准备](#开始前你需要准备)
- [构建与运行](#构建与运行)
- [验证](#验证)
- [实测结论](#实测结论)
- [FAQ](#faq)
- [License](#license)

## 三个实现

| 目录 | 语言 | 工程 | 测试 |
|------|------|------|------|
| [`objc/`](objc/) | Objective-C | 单文件 `src/main.m` + Makefile | e2e 脚本 |
| [`rs/`](rs/) | Rust | Cargo（lib + bin），FFI 经 C shim | 21 单元/集成测试 |
| [`swift/`](swift/) | Swift | SwiftPM（lib + bin），直调 ES API | 21 XCTest |

三版行为完全等价，共用同一套验证脚本（`scripts/test-e2e.sh`、`scripts/test-cache.sh`）。

## 开始前你需要准备

本项目依赖 Apple 的**托管权限**（managed entitlement），无法即下即用，请按序准备：

1. **Apple Developer Program 付费账号**（个人或公司均可）。
2. **申请 Endpoint Security entitlement**（托管权限，需 Apple 审批）：
   在 [Apple 开发者权限申请页](https://developer.apple.com/contact/request/) 提交
   Endpoint Security 申请并说明用途，审批通常需要数个工作日。
3. **创建签名物料**（审批通过后）：
   - [开发者后台](https://developer.apple.com/account/resources) → Identifiers → 新建 App ID
     （如 `com.example.esmvp`），Capabilities 勾选 **Endpoint Security**；
   - Profiles → 新建 → 类型选 **Developer ID** → 关联该 App ID → 下载
     `.provisionprofile`，放入对应实现的 `packaging/` 目录并命名为 `esmvp.provisionprofile`；
   - 本机钥匙串需有 **Developer ID Application** 证书（Xcode → Settings → Accounts 可生成）。
4. **macOS 13.0+**，SIP 保持开启，无需任何开发者模式开关。

> [!IMPORTANT]
> `com.apple.developer.endpoint-security.client` 由 amfid 在进程启动时校验 embedded
> provisioning profile，没有 profile 将无法创建 ES client。本仓库不包含任何
> profile / 证书——它们属于你的开发者账号，且已被 `.gitignore` 排除。

## 构建与运行

以 ObjC 版为例（`rs/`、`swift/` 同理，入口均为各自目录下的 `make` 目标）：

```bash
cd objc
make package                                   # 编译 + 签名（自动探测本机 Developer ID 证书）
sudo ./ESMvp.app/Contents/MacOS/esmvp --watch ~/es-test --verbose
```

可变参数用环境变量覆盖：

```bash
BUNDLE_ID=com.example.esmvp \
PROFILE=./packaging/esmvp.provisionprofile \
IDENTITY="Developer ID Application: Your Name (TEAMID)" \
./scripts/build.sh
```

**首次运行前还需一步**：系统设置 → 隐私与安全性 → **完全磁盘访问权限** → 添加构建出的
App 并打开开关（Developer ID 形态的 ES client 必需；企业部署可用 MDM 下发 PPPC 配置免除）。
授权按签名身份（bundle ID + 证书）记账，之后重新编译无需重复授权。

## 验证

```bash
make e2e          # DoD 用例：mute-all 零事件 / watch 目录内 png 被拦截、目录外不受影响
make e2e-cache    # 内核授权缓存语义验证（Case A–F）
```

`rs/` 与 `swift/` 另有不依赖 root 的单元测试：`cargo test` / `swift test`。

## 实测结论

完整数据见 [SPEC.md §8](SPEC.md#8-实测结论)，要点：

| 语义 | 实测结论 |
|------|----------|
| inversion + AUTH 订阅 | ✅ 成立（invert 之后订阅 `AUTH_OPEN` 正常工作） |
| 目录外事件 | 零上送，事件量与整机负载解耦 |
| 授权缓存键 | ≈（可执行文件 × 目标文件 × open flags），ALLOW 不跨可执行文件外溢 |
| 缓存失效 | 文件被修改 → 自动失效，下次 open 重新上送 |
| DENY | 永不缓存，每次拦截可观测 |

## FAQ

- **`es_new_client 失败 rc=5`**：需要 root，用 `sudo` 运行。
- **rc=4**：缺少完全磁盘访问授权（见[构建与运行](#构建与运行)末节）。
- **rc=3**：签名缺少 ES entitlement——检查 embedded profile 与 App ID、capability 是否匹配。
- **改了 bundle ID 后 rc=4**：FDA 授权按签名身份记账，换 ID 需重新授权一次。
- **杀掉进程后拦截失效**：属预期——client 消失即授权默认放行，无任何残留状态。

## License

[MIT](LICENSE) © es-mvp contributors
