// main.swift — Swift 宿主调用 Rust staticlib（Swift → Rust 方向）
//
// 三种调用面：
//   1. 纯函数 rust_core_add（C ABI 最简形态）
//   2. 跨语言字符串所有权：rust_core_greeting 分配 → Swift 读取 → 归还释放
//   3. 回调：Swift 的 @convention(c) 函数传给 Rust，Rust 反向调用 Swift
//
// 编译：见 build.sh（swiftc 直接链接 librust_core.a + 头文件）

import Foundation

// 1) 纯计算
let sum = rust_core_add(40, 2)
print("rust_core_add(40,2) = \(sum)")

// 2) 字符串所有权往返
let raw = rust_core_greeting()
let greeting = String(cString: raw!)
print("rust_core_greeting() = \(greeting)")
rust_core_free_string(UnsafeMutablePointer(mutating: raw))

// 3) Rust 回调 Swift（@convention(c) 函数指针，无捕获）
let cb: @convention(c) (Int64) -> Int64 = { x in x + 100 }
let applied = rust_core_apply(7, cb)
print("rust_core_apply(7, +100) = \(applied)")

// 4) NULL 回调防御
let noCb = rust_core_apply(7, nil)
print("rust_core_apply(7, nil) = \(noCb)")

// 供 verify.sh 断言
let pass = sum == 42 && greeting.contains("hello from Rust core") &&
    applied == 121 && noCb == -1
print(pass ? "SWIFT_HOST_PASS" : "SWIFT_HOST_FAIL")
exit(pass ? 0 : 1)
