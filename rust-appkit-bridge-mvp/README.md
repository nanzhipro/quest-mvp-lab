# rust-appkit-bridge-mvp

Rust ↔ AppKit / ObjC / Swift 桥接 MVP —— 以 `libs/cua-driver/rust/crates/platform-macos`
为蓝本，用最小可运行工程复刻其全部桥接手法，并双向打通 Swift。

## 验证结论（本机实测）

```text
Rust → AppKit/ObjC/Swift  (bridge-demo):   demo_pass=True   （6/6 层通过）
Swift → Rust             (rust-host-demo):  SWIFT_HOST_PASS （4/4 断言通过）
cargo test（bridge-demo）:  1 passed
cargo test（rust-core）:    3 passed
```

## 目录结构

```text
rust-appkit-bridge-mvp/
├── Makefile                 # make / make run / make clean
├── scripts/verify.sh        # 端到端断言（demo_pass + SWIFT_HOST_PASS + 退出码）
├── swift-lib/               # Rust → Swift 方向：SwiftBridge.dylib
│   ├── SwiftBridge.swift    #   @_cdecl 函数 + @objc 类 + @convention(block) 方法
│   └── build.sh             #   swiftc -emit-library
├── rust/                    # Rust → AppKit/ObjC 方向：bridge-demo 二进制
│   ├── build.rs             #   框架链接 + SDK 搜索路径（platform-macos 同款）
│   └── src/
│       ├── main.rs          #   逐层执行，输出 JSON + demo_pass
│       └── layers/          #   六个桥接层，每层对应 platform-macos 真实用法
├── swift-host/              # Swift → Rust 方向：rust-host-demo 二进制
│   ├── rust-core/           #   Rust staticlib（C ABI，cua_driver_abi.h 同思路）
│   ├── rust_core.h          #   手写 C 头文件
│   ├── main.swift           #   Swift 宿主：纯函数/字符串所有权/回调三面
│   └── build.sh
└── docs/MVP_NOTES.md        # 详细技术备注（每层原理 + 坑 + 验证证据）
```

## 七个桥接层一览

| 层 | 手法 | platform-macos 对应 | 验证点 |
|---|---|---|---|
| L1 | objc2 类型化安全绑定 | `apps/nsworkspace.rs` | NSWorkspace/NSRunningApplication/NSString/NSURL |
| L2 | `msg_send!` 裸消息 + 动态类查找 | `cursor/overlay.rs` run_appkit | NSScreen frame、AnyClass::get("NSWorkspace") |
| L3 | block2::RcBlock 回调 | `apps/nsworkspace.rs` make_completion_block | 通知观察者 + 启动应用 completion handler 同步化 |
| L4 | 裸 C FFI（#[link] + core-graphics crate） | `session.rs` / `ax/bindings.rs` / `get_screen_size.rs` | SessionGetInfo / AXIsProcessTrusted / CGEvent |
| L5 | dlopen + dlsym + objc_getClass 驱动 Swift dylib | `input/skylight.rs`（SkyLight 私有框架手法） | @_cdecl、@objc 类、block 往返、Swift 内部调 AppKit |
| L6 | dispatch_async_f 主线程分发 + CFRunLoop 泵 | `cursor/overlay.rs`、`pip/mod.rs` | 后台线程 → 主队列载荷送达 |
| L7 | Swift 宿主经 C ABI 调 Rust staticlib | `include/cua_driver_abi.h`、`examples/embedded-host-macos` | 纯函数/字符串所有权/函数指针回调 |

## 快速开始

```bash
make run        # 构建全部 + 运行两个 demo + 断言
make            # 仅构建
```

需要：macOS（本机 26.x 实测）、Xcode CLT（swiftc）、Rust 1.82+。

## 关键坑速查（详见 docs/MVP_NOTES.md）

1. **+0/+1 内存管理**：`addObserverForName:...` 返回 +0 借用指针，removeObserver 后
   再 release = use-after-free 崩溃（本 MVP 实测踩中，EXC_BREAKPOINT in object_getClass）。
2. **MainThreadMarker**：cargo test 的 worker 线程不是 pthread 主线程，
   AppKit 类型化 API 在测试里直接 panic → 桥接验证必须走真实二进制。
3. **RefEncode 边界**：msg_send! 传 block 要 `&*rcblock`（RefEncode 实现在
   `Block<F>` 而非 `RcBlock<F>`）；传对象要 `&*retained`。
4. **CGEventSetLocation**：core-graphics crate 没绑定，platform-macos 也手写 FFI。
5. **dlopen 句柄**：OnceLock 要求 Send+Sync，裸指针存 usize。
