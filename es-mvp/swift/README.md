# esmvp-swift

目录级 AUTH_OPEN 管控的 Swift 实现：基于 Endpoint Security 静音反转（`es_invert_muting`），
只接收 watch 目录内的文件打开事件，按 MIME 裁决（png/jpg 拒绝打开），可选内核授权缓存。
设计背景、实测结论与签名前置条件见 `../SPEC.md`（§9 为真机验证结论）。

与 objc/rs 版行为完全等价；Swift 直接调用 ES C API（闭包自动桥接 block），无需 C shim。

## 快速开始

```bash
make help        # 查看全部工程入口
make test        # XCTest 单元测试 + Mock 集成测试（无需 root）
make package     # release 构建 + 签名打包 ESMvpSwift.app
make e2e         # DoD 用例验证（sudo，需先完成 FDA 授权）
make e2e-cache   # 内核授权缓存语义验证 Case A–F（sudo）
```

直接运行：

```bash
sudo ./ESMvpSwift.app/Contents/MacOS/esmvp-swift --watch ~/es-test --cache --verbose
```

前置条件（一次性）：ES entitlement 的 provisioning profile（已自持于
`packaging/esmvp_profile.provisionprofile`）+ 系统设置中对本 App 授予完全磁盘访问权限
（bundle ID 与 objc/rs 版相同，授权互通）。

## 目录结构

```
├── Package.swift             # SwiftPM 清单（macOS 13+，链接 libEndpointSecurity）
├── Makefile                  # 统一工程入口
├── Sources/
│   ├── ESMvpCore/            # 全部可测逻辑（library target）
│   │   ├── Cli.swift         #   swift-argument-parser 参数定义（纯解析）
│   │   ├── Config.swift      #   watch 目录规范化（realpath + 尾斜杠）、运行模式
│   │   ├── Decision.swift    #   裁决引擎：MIME → Allow/Deny（纯函数）
│   │   ├── Backend.swift     #   EsBackend 协议；RealEs（ES API）/ MockEs（内存实现）
│   │   ├── App.swift         #   编排：初始化序列 + 事件处理 + 统计/信号
│   │   ├── Stats.swift       #   锁保护计数 + 快照
│   │   └── Log.swift         #   极简控制台日志（无 ANSI，可 grep）
│   └── esmvp-swift/main.swift
├── Tests/ESMvpCoreTests/     # XCTest：21 个用例（与 rs 版一一对应）
├── packaging/                # 签名资产：Info.plist / entitlements / provisionprofile
└── scripts/                  # build.sh（打包签名）、test-e2e.sh、test-cache.sh
```

设计要点（与 rs 版一致）：

- **后端可替换**：编排层只依赖 `EsBackend` 协议，`MockEs` 回放事件支撑无 root 测试。
- **初始化序列不可乱**（Apple 头文件约束）：`es_new_client` → 清默认 target mute set →
  invert → 自检 → 应用目录规则 → **最后** `es_subscribe(AUTH_OPEN)`。
- **AUTH_OPEN 必须 `es_respond_flags_result` 应答**（flags=0 即拒绝）；DENY 永不写缓存。
- inversion 语义下静音规则即白名单：无 `--watch` = 全部 AUTH_OPEN 内核侧静音。

## 测试

```bash
swift test
```
