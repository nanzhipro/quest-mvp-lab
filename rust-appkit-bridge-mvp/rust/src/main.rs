//! bridge-demo — Rust ↔ AppKit / ObjC / Swift 桥接 MVP 演示
//!
//! 七个桥接层，每一层都对应 libs/cua-driver/rust/crates/platform-macos
//! 中的真实用法：
//!
//!   L1 objc2 安全绑定      ← apps/nsworkspace.rs
//!   L2 raw msg_send!       ← cursor/overlay.rs run_appkit、permissions/panel.rs
//!   L3 block2 回调         ← apps/nsworkspace.rs make_completion_block
//!   L4 裸 C FFI            ← session.rs / ax/bindings.rs / tools/get_screen_size.rs
//!   L5 dlopen + Swift dylib← input/skylight.rs（SkyLight 私有框架加载手法）
//!   L6 主线程分发          ← cursor/overlay.rs、pip/mod.rs（dispatch_async_f）
//!   L7 Swift 宿主调 Rust   ← 见 swift-host/（cua_driver_abi.h 的 C ABI 手法）
//!
//! 输出：单行 JSON（每层 ok 布尔 + 详情 + 总 demo_pass）。

mod layers;

use serde_json::json;

fn main() {
    // 全部层必须在主线程执行（AppKit 约束），demo 二进制天然满足。
    let mut results = serde_json::Map::new();
    let mut all_ok = true;

    for (name, run) in [
        (
            "l1_objc2_safe",
            layers::l1_objc2_safe::run as fn() -> serde_json::Value,
        ),
        ("l2_raw_msg_send", layers::l2_raw_msg_send::run),
        ("l3_blocks", layers::l3_blocks::run),
        ("l4_c_ffi", layers::l4_c_ffi::run),
        ("l5_swift_dylib", layers::l5_swift_dylib::run),
        ("l6_main_thread", layers::l6_main_thread::run),
    ] {
        let value = run();
        let ok = value.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
        all_ok &= ok;
        results.insert(name.to_string(), value);
    }

    results.insert("demo_pass".to_string(), json!(all_ok));

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::Value::Object(results)).unwrap()
    );

    // 退出码供脚本断言
    std::process::exit(if all_ok { 0 } else { 1 });
}

// ── 测试策略说明 ──
// 桥接层依赖 AppKit 主线程（MainThreadMarker），而 cargo test 的 worker 线程
// 不是 pthread 主线程（platform-macos 的 tools/get_screen_size.rs 踩过同一个坑：
// tokio 线程上 MainThreadMarker::new() 恒为 None）。因此：
//   - 纯逻辑测试 → rust-core 的 #[cfg(test)]（无 AppKit 依赖）
//   - 桥接端到端验证 → scripts/verify.sh（运行真实二进制，断言 JSON 与退出码）
// 这里只保留一个编译期冒烟：确保 layers 模块可以实例化。
#[cfg(test)]
mod tests {
    use super::layers;

    #[test]
    fn layers_module_compiles_and_ffi_resolves() {
        // 无需主线程的层：C FFI 层全线程安全（session.rs 同款 API）
        let l4 = layers::l4_c_ffi::run();
        assert!(l4.get("ok").and_then(|v| v.as_bool()).unwrap_or(false));
        // l1/l2/l3/l5/l6 需主线程：由 verify.sh 端到端覆盖
    }
}
