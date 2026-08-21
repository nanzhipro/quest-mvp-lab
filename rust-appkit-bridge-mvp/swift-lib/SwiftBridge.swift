// SwiftBridge.swift — Swift 侧桥接库（Rust → Swift 方向）
//
// 三个导出面，分别对应 platform-macos 中三类“调 Swift/ObjC”手段：
//
// 1. @_cdecl C 函数        → Rust 端 dlsym 解析（对应 skylight.rs 的 find_sym）
// 2. @objc 类 + 方法       → Rust 端 objc_getClass + objc_msgSend 驱动
//                            （对应 skylight.rs 驱动 SLSEventAuthenticationMessage 的方式）
// 3. @convention(block)    → Rust 端 block2::RcBlock 传入，Swift 回调（双向往返）
//
// 编译产物 libSwiftBridge.dylib 由 Rust 端在运行时 dlopen（RTLD_NOW | RTLD_GLOBAL），
// 与 platform-macos 加载私有框架 SkyLight 的手法完全一致。

import Foundation
import AppKit

// ── 1. @_cdecl：纯 C 符号，Rust 经 dlsym 直接调用 ──────────────────────────

@_cdecl("swift_bridge_add")
public func swiftBridgeAdd(_ a: Int64, _ b: Int64) -> Int64 {
    a + b
}

// 返回指向静态缓冲区的 C 字符串（不泄漏、无需释放）。
private let greetingStorage: [CChar] =
    Array("hello from Swift (via @_cdecl)".utf8CString)

@_cdecl("swift_bridge_greeting")
public func swiftBridgeGreeting() -> UnsafePointer<CChar>? {
    greetingStorage.withUnsafeBufferPointer { buf in
        UnsafePointer(buf.baseAddress!)
    }
}

// ── 2. @objc 类：进入 ObjC runtime，Rust 用 objc_getClass + msg_send 驱动 ──

@objc(SwiftBridgeService)
public final class SwiftBridgeService: NSObject {

    /// 纯计算：selector `add:to:`，Rust 端 msg_send![svc, add: 20 to: 22]
    @objc public func add(_ a: Int64, to b: Int64) -> Int64 {
        a + b
    }

    /// 字符串桥接：selector `upper:`，NSString 进出 runtime
    @objc public func upper(_ s: String) -> String {
        s.uppercased()
    }

    /// Swift → AppKit：调用 NSScreen（AppKit 要求主线程，MVP 演示在 main 线程跑）
    @objc public func mainScreenDescription() -> String {
        guard let screen = NSScreen.main else { return "no main screen" }
        let f = screen.frame
        let scale = screen.backingScaleFactor
        return String(format: "%.0fx%.0f @ %.1fx", f.width, f.height, scale)
    }

    /// Block 参数：selector `computeAsync:completion:`。
    /// Rust 端传入 block2::RcBlock<dyn Fn(i64)>，Swift 同步回调 → 完整往返。
    @objc public func computeAsync(
        _ input: Int64,
        completion: @escaping @convention(block) (Int64) -> Void
    ) {
        completion(input * 2)
    }
}
