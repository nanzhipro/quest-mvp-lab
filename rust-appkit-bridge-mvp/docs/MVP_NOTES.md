# MVP 技术备注（MVP_NOTES）

本文档逐层说明 MVP 的实现原理、与 `platform-macos` 的对应关系、编译/运行中实测
踩到的坑，以及每层验证证据。所有结论均在本机（macOS 26.5.2 arm64, Xcode 16.x,
Swift 6.3.2, rustc 1.96.0）实测复现。

---

## 0. 总体架构：为什么需要桥接

`platform-macos` 是一个纯 Rust crate，但它实现了完整的 macOS 桌面自动化：
AX 树遍历、后台鼠标键盘注入、窗口枚举、录屏、悬浮光标。macOS 的系统能力
（AppKit/Foundation/ApplicationServices/SkyLight/ScreenCaptureKit）几乎全部
以 ObjC 或 C 形态暴露，Swift 框架则通过 ObjC runtime 互操作。所以该 crate 的
本质是一本「Rust 如何调用 macOS 系统 API」的活字典，共用了六类桥接手段。

MVP 把这六类手段各做一个最小可运行层，外加一层反向（Swift 调 Rust），
共七层。每一层代码都标注了在 platform-macos 中的对应文件。

---

## L1 — objc2 类型化安全绑定（对应 apps/nsworkspace.rs）

**原理**：`objc2` 系列 crate 是 ObjC runtime 的安全封装。`objc2-foundation` /
`objc2-app-kit` 用 `extern_class!`/`extern_methods!` 宏为每个 AppKit/Foundation
类生成类型化 Rust API：`Retained<T>` 管理引用计数（Drop 时自动 release），
`MainThreadMarker` 在编译期标记主线程约束，`NSString::from_str` 等完成
Rust ↔ ObjC 字符串转换。Cargo feature 按需开启类（如 `features = ["NSWorkspace"]`）。

**MVP 代码**：`rust/src/layers/l1_objc2_safe.rs`
- `NSWorkspace::sharedWorkspace()` 取单例
- `NSRunningApplication::runningApplicationsWithBundleIdentifier` 查 Finder PID
- `NSString` 往返、`NSURL::fileURLWithPath` 构造

**验证**：`finder_pids: [624]`（真实进程）、`string_roundtrip` 相等。

**坑**：
1. **MainThreadMarker 不是运行时检查而是编译期令牌**：`MainThreadMarker::new()`
   返回 `Option`，非主线程时为 `None` → 必须 `expect` 或提前降级。
   `get_screen_size.rs` 就是因为 tokio 线程拿不到 MainThreadMarker，把 NSScreen
   实现整体换成了线程安全的 CGDisplay API（`main_screen_size()` 注释原文）。
2. **feature 缺了编译直接报 `E0599: no function or associated item named 'new'`**
   —— MainThreadMarker 需要 `NSThread` feature，第一次编译就踩中。

---

## L2 — raw objc_msgSend（对应 cursor/overlay.rs run_appkit、permissions/panel.rs）

**原理**：绑定未覆盖的 API 用 `msg_send![receiver, selector: arg]` 直接发消息。
`class!(NSClassName)` 展开为 `objc_getClass`，selector 由宏拼装。
**返回类型必须与 ObjC 类型编码严格一致**：`frame` 返回结构体 `NSRect`
（objc2 知道如何从 x0 寄存器读回），`setActivationPolicy:` 返回 BOOL 而不是
void（overlay.rs:585 注释专门强调），读成 void 会让寄存器解读错位。

**MVP 代码**：`rust/src/layers/l2_raw_msg_send.rs`
- `msg_send![class!(NSScreen), mainScreen]` → `frame` → `backingScaleFactor`
- `AnyClass::get("NSWorkspace")` 动态类查找（skylight.rs `objc_class()` 的 objc2 等价物）
- `stringWithUTF8String:` + `Retained::retain` 接管 +1 对象

**验证**：`screen_points: 1512x982 @ 2.0x`（Retina 实测）、`dyn_string: "raw msg_send"`。

**坑**：
1. **objc2 0.5 的 `msg_send!` 不提供 `nil` 字面量**（E0425）—— 用
   `std::ptr::null_mut::<AnyObject>()`。
2. **`&Retained<T>` 不实现 `RefEncode`**（E0277）—— 参数要 `&*retained`
   解引用到 `&T`。
3. **方法返回 +1 对象时用 `Retained::retain(ptr)` 接管**，否则泄漏或
   double-release；objc2 0.5 中 `retain` 是 unsafe fn，必须包 unsafe 块。

---

## L3 — ObjC Block 桥接（对应 apps/nsworkspace.rs make_completion_block）

**原理**：AppKit/Foundation 的异步 API（completion handler、通知观察者）用
ObjC Block。Rust 侧用 `block2::RcBlock` 构造等价 Block：闭包被放进 Block 的
`__invoke` 函数指针，跨 FFI 调用时 ObjC 侧直接调用。`RcBlock::new` 要求
闭包 `'static`，捕获必须 move。

**MVP 代码**：`rust/src/layers/l3_blocks.rs`
- (a) `NSNotificationCenter` 块观察者：`addObserverForName:...usingBlock:`
- (b) NSWorkspace 启动应用 completion handler：完整复刻 nsworkspace.rs 的
  `RcBlock` + `mpsc::sync_channel` 同步化 + 30s 超时 + `runningApplications`
  进程对账 fallback（LaunchServices 可能接受请求但不回调后台进程）。

**验证**：通知 block `fired_count: 1`；Calculator 启动成功 `calculator_pid: 86834`。

**坑（本 MVP 最大的一次崩溃，实测踩中）**：
1. **+0 借用指针 release = use-after-free**：`addObserverForName:object:queue:
   usingBlock:` 的返回 token 是 **+0**（autoreleased 借用；头文件无
   `NS_RETURNS_RETAINED`），强引用只在通知中心内部。先 `removeObserver:`
   （中心释放自己那份引用）再对 token 手动 `release`，就是对已释放对象发消息：
   `EXC_BREAKPOINT (brk #0xc472) in libobjc object_getClass`（objc_msgSend
   的 isa 校验 trap）。正确 teardown：**只 removeObserver，绝不 release**。
   教训：ObjC 内存管理约定（alloc/new/copy 前缀决定 +1，其余 +0）在 Rust 侧
   必须逐 API 核对，不能想当然。
2. **RcBlock::new 闭包必须是 'static**：局部捕获用 `Arc` 共享而不是闭包借用。
3. **msg_send! 传 block 要 `&*block`**（RefEncode 实现在 `Block<F>` 上，
   `RcBlock<F>` 没有）——这是 `RefEncode is not satisfied` 报错的根因。

---

## L4 — 裸 C FFI（对应 session.rs / ax/bindings.rs / tools/get_screen_size.rs）

**原理**：macOS 大量系统 API 是纯 C：`Security.framework` 的 `SessionGetInfo`、
`ApplicationServices.framework` 的 AX 系列、CoreGraphics 的 CG 系列。
Rust 用 `#[link(name = "...", kind = "framework")] extern "C"` 直接声明，
或使用封装 crate（`core-foundation` / `core-graphics` / `foreign-types`）。

**MVP 代码**：`rust/src/layers/l4_c_ffi.rs`
- `SessionGetInfo`（security session 探测，session.rs #1724 同款防护逻辑）
- `AXIsProcessTrusted()`（只读，不弹窗）
- `CGMainDisplayID` + `CGDisplayBounds`（线程安全，无需主线程）
- `CGEvent` 构造 + 手写 `CGEventSetLocation` FFI

**验证**：`graphic_session_access: true`（GUI 会话）、`ax_trusted: false`
（本进程未授权，符合预期）、`cgevent_construct: true`。

**坑**：
1. **core-graphics 0.24 没有 `set_location`**：`CGEvent::set_location` 不存在，
   platform-macos 自己手写了 `CGEventSetLocation` FFI（interactive.rs:743），
   MVP 照搬。这类「crate 没绑定的 C API」是常态，手写 `#[link]` 是标准解法。
2. **`CGEvent::as_ptr` 来自 `foreign_types::ForeignType` trait**，不 import 就
   E0599。
3. **`CGEventSource`/`CGEventSourceStateID` 在 `event_source` 模块**，
   不在 `event` 模块（E0432/E0603）。

---

## L5 — dlopen + dlsym + objc_getClass 驱动 Swift dylib（对应 input/skylight.rs）

**原理（本 MVP 的核心验证）**：SkyLight 是私有框架，不能链接，platform-macos
用 `dlopen` 加载 + `dlsym(RTLD_DEFAULT)` 解析符号 + `objc_getClass` 找私有类
+ 裸 `objc_msgSend` 驱动。Swift 代码编译成 dylib 后与私有框架完全同构：
`@_cdecl` 导出 C 符号、`@objc` 类注册进 ObjC runtime。因此 **Rust 驱动 Swift
代码 = 驱动 SkyLight 的同一套手法**。

**MVP 代码**：`rust/src/layers/l5_swift_dylib.rs` + `swift-lib/SwiftBridge.swift`
- `swiftc -emit-library` 产出 dylib，Rust 运行时 `dlopen(RTLD_NOW|RTLD_GLOBAL)`
- `dlsym` 解析 `swift_bridge_add` / `swift_bridge_greeting`（@_cdecl）
- `AnyClass::get("SwiftBridgeService")` + `msg_send![cls, new]` 实例化 @objc 类
- `computeAsync:completion:` 传入 `RcBlock`，Swift 同步回调 → **完整往返**
- Swift 方法内部调 `NSScreen.main` → 链路
  **Rust → ObjC runtime → Swift → AppKit → Swift → Rust**

**验证**：
```json
"cdecl": { "swift_bridge_add(20,22)": 42, "swift_bridge_greeting": "hello from Swift (via @_cdecl)" },
"objc_class": { "add(20,22)": 42, "upper(bridge)": "BRIDGE",
                "swift->appkit": "1512x982 @ 2.0x", "block_roundtrip": 42 }
```

**坑**：
1. **dlopen 句柄不能进 `OnceLock<*mut c_void>`**（*mut c_void 非 Send/Sync，
   E0277）—— 存 `usize`。
2. **Swift 必须 `@objc(ClassName)` 显式命名**才能在 ObjC runtime 里用
   稳定名字找到（Swift 默认带模块前缀/混淆）。
3. **`@_cdecl` 函数返回 C 字符串要指向静态存储或堆**，Swift String 不会
   自动转换生命周期；MVP 用静态 `[CChar]` 数组避免泄漏。
4. **Swift 6 的 `@convention(block)` 参数要 `@escaping`**，否则编译报错。
5. screencapturekit 走的也是同一条 Swift 互操作路（build.rs 里为它补 Swift
   runtime rpath：`/usr/lib/swift` + Xcode 工具链 lib/swift/macosx）。

---

## L6 — 主线程分发（对应 cursor/overlay.rs、pip/mod.rs）

**原理**：AppKit 必须跑在主线程；tokio/MCP 服务器在后台线程。两边的桥梁是
libdispatch：后台线程 `dispatch_async_f(主队列, Box 载荷, C 回调)`，主线程
在 `NSApplication.run()` 里泵 RunLoop（真实 App）或 `CFRunLoopRunInMode`
轮询（CLI）。`dispatch_get_main_queue()` 是内联函数，取它背后的导出符号
`_dispatch_main_q` 最稳。

**MVP 代码**：`rust/src/layers/l6_main_thread.rs`
- 后台线程 `dispatch_async_f(&raw const _dispatch_main_q, ...)`
- 主线程 `CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, false)` 泵 5s
- 回调里 `Box::from_raw` 回收载荷，写回值 + 完成标记

**验证**：`delivered_value: 42`、`main_queue_dispatched: true`。

**坑**：
1. **`#[link(name = "dispatch", kind = "dylib")]` 需要 SDK 搜索路径**：
   build.rs 里 `rustc-link-search={SDKROOT}/usr/lib/system`（platform-macos
   build.rs 注释说明）。
2. **`kCFRunLoopDefaultMode` 是导出数据符号**（CFStringRef 常量），不是
   `CFSTR` 宏的结果——Rust 里 `extern "C" { static kCFRunLoopDefaultMode:
   *const c_void; }` 直接引用。
3. **CLI 不泵 RunLoop 则主队列 block 永不执行**——死等标记会挂死；
   必须 `CFRunLoopRunInMode` 分片轮询 + 超时兜底。

---

## L7 — Swift 宿主经 C ABI 调 Rust staticlib（对应 include/cua_driver_abi.h、examples/embedded-host-macos）

**原理**：反向桥接。Rust 编成 `staticlib`，`#[no_mangle] pub extern "C"`
导出稳定 ABI；Swift 侧用 `-import-objc-header` 导入 C 头文件后直接调用。
cua-driver 的完整 SDK 就是这么暴露的（`cua_driver_abi.h` 由 cua-driver-abi-header
从 `cua-driver-sdk/src/abi.rs` 生成）。更进一步的跨语言方案（UniFFI / MCP）
见调研报告 §5.3。

**MVP 代码**：`swift-host/`
- `rust_core_add`：纯函数
- `rust_core_greeting` / `rust_core_free_string`：跨语言字符串所有权
  （Rust 分配 → Swift 读取 → 归还释放）
- `rust_core_apply(x, cb)`：**函数指针回调** —— Rust 反向调用 Swift 传入的
  `@convention(c)` 函数；`nil` 回调防御返回 -1

**验证**：
```text
rust_core_add(40,2) = 42
rust_core_greeting() = hello from Rust core (staticlib, C ABI)
rust_core_apply(7, +100) = 121
rust_core_apply(7, nil) = -1
SWIFT_HOST_PASS
```

**坑**：
1. **swiftc 不吃裸头文件**（`error: unexpected input file: rust_core.h`），
   必须 `-import-objc-header`。
2. **staticlib 链接无需 rpath**（静态进二进制），比 cdylib 省事；
   dylib 方案要在运行期处理 install_name/rpath。
3. Swift 侧 C 函数映射：`@convention(c)` 闭包可直接作为函数指针参数；
   字符串用 `String(cString:)` 读、`UnsafeMutablePointer(mutating:)` 归还。

---

## 构建/测试命令速查

```bash
make run                 # 全量构建 + 端到端断言（两条 demo 都跑）
make                     # 仅构建
cd rust && cargo test --release          # 1 passed（L4 冒烟；其余层需主线程）
cd swift-host/rust-core && cargo test    # 3 passed（纯逻辑）
./scripts/verify.sh      # demo_pass + SWIFT_HOST_PASS + 退出码断言
```

## 测试策略说明（重要）

桥接层依赖 AppKit 主线程（`MainThreadMarker`），而 **cargo test 的 worker
线程不是 pthread 主线程**——`MainThreadMarker::new()` 恒为 `None`，
AppKit 类型化 API 直接 panic（本 MVP 实测：`all_layers_report_ok` 测试
在 l1 就 panic）。这与 platform-macos 的处境一致：
- 纯逻辑（无 AppKit）→ cargo test
- 桥接端到端 → 真实二进制 + 断言脚本（verify.sh / repo 的 E2E harness）

## 环境与版本

| 组件 | 版本 |
|---|---|
| macOS | 26.5.2 (arm64) |
| Xcode | /Applications/Xcode.app（swiftc 6.3.2） |
| rustc / cargo | 1.96.0 |
| objc2 / objc2-foundation / objc2-app-kit | 0.5.2 / 0.2.2 / 0.2.2（与 cua-driver Cargo.lock 一致） |
| block2 | 0.5.1 |
| core-graphics / core-foundation | 0.24 / 0.10 |
