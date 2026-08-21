# esmvp-rs

目录级 AUTH_OPEN 管控的 Rust 实现：基于 Endpoint Security 静音反转（`es_invert_muting`），
只接收 watch 目录内的文件打开事件，按 MIME 裁决（png/jpg 拒绝打开），可选内核授权缓存。
设计背景、实测结论与签名前置条件见 `../SPEC.md`（§9 为真机验证结论）。

## 快速开始

```bash
make help        # 查看全部工程入口
make test        # 单元测试 + Mock 集成测试（无需 root）
make lint        # clippy（告警即失败）
make package     # release 构建 + 签名打包 ESMvpRs.app
make e2e         # DoD 用例验证（sudo，需先完成 FDA 授权）
make e2e-cache   # 内核授权缓存语义验证 Case A–F（sudo）
```

直接运行：

```bash
sudo ./ESMvpRs.app/Contents/MacOS/esmvp-rs --watch ~/es-test --cache --verbose
```

前置条件（一次性）：ES entitlement 的 provisioning profile（已自持于
`packaging/esmvp_profile.provisionprofile`）+ 系统设置中对本 App 授予完全磁盘访问权限
（bundle ID 与 ObjC 版相同，授权互通）。

## 目录结构

```
├── Cargo.toml / Cargo.lock   # 依赖锁定
├── Makefile                  # 统一工程入口（build/test/lint/fmt/package/e2e/clean）
├── build.rs                  # 编译 C shim、链接 libEndpointSecurity
├── rustfmt.toml              # 格式化约定（edition 2024，max_width 100）
├── src/                      # Rust 源码（见下）
├── csrc/                     # C shim：es_message_t 字段提取 + blocks 桥接（无业务逻辑）
├── packaging/                # 签名资产：Info.plist / entitlements / provisionprofile
└── scripts/                  # build.sh（打包签名）、test-e2e.sh、test-cache.sh
```

## 源码结构（src/）

```
main.rs      入口：日志初始化 → CLI 解析 → app::run
cli.rs       clap 参数定义（纯解析，无语义）
config.rs    watch 目录规范化（realpath + 尾斜杠）、运行模式
app.rs       编排：ES 初始化序列（顺序受 Apple 约束）+ 事件处理 + 统计/信号
backend.rs   EsBackend trait 抽象；RealEs（FFI）/ MockEs（内存实现，测试用）
decision.rs  裁决引擎：MIME → Allow/Deny，应答 flags 与缓存策略派生（纯函数）
stats.rs     无锁计数 + 快照
ffi.rs       libEndpointSecurity 符号声明（全部不透明指针）
```

设计要点：

- **后端可替换**：编排层只依赖 `EsBackend` trait，单元测试用 `MockEs` 回放事件，
  无需 root 即可覆盖初始化序列、裁决链路、应答参数与统计。
- **初始化序列不可乱**（Apple 头文件约束）：`es_new_client` → 清默认 target mute set →
  invert → 自检 → 应用目录规则 → **最后** `es_subscribe(AUTH_OPEN)`。
- **AUTH_OPEN 必须 `es_respond_flags_result` 应答**（flags=0 即拒绝）；DENY 永不写缓存。
- inversion 语义下静音规则即白名单：无 `--watch` = 全部 AUTH_OPEN 内核侧静音。
- 日志输出关闭 ANSI 转义，重定向到文件后仍可直接 grep/被脚本消费。
