//! Layer 1 — objc2 安全绑定（typed bindings）
//!
//! 对应 platform-macos 中 apps/nsworkspace.rs 的用法：
//! `NSWorkspace::sharedWorkspace()`、`NSRunningApplication::runningApplicationsWithBundleIdentifier`
//! `NSString::from_str`、`NSURL::fileURLWithPath` 等类型化 API。
//! 编译期类型检查 + Retained 所有权 + 自动引用计数，是默认首选路径。

use objc2_app_kit::{NSRunningApplication, NSWorkspace};
use objc2_foundation::{MainThreadMarker, NSString, NSURL};
use serde_json::json;

pub fn run() -> serde_json::Value {
    // AppKit 类型化 API 要求主线程；MainThreadMarker 是零尺寸编译期令牌，
    // 在非主线程构造会直接 panic（与 overlay.rs run_appkit 一致）。
    let _mtm = MainThreadMarker::new().expect("Layer1 必须在主线程运行");

    // NSWorkspace 单例（类型化）
    let ws = unsafe { NSWorkspace::sharedWorkspace() };
    let ws_desc = format!("NSWorkspace@{:p}", ws.as_ref());

    // 类型化查询：Finder 的运行实例（进程级 API，无 TCC 要求）
    let finder_bid = NSString::from_str("com.apple.finder");
    let finder_apps =
        unsafe { NSRunningApplication::runningApplicationsWithBundleIdentifier(&finder_bid) };
    let finder_pids: Vec<i64> = unsafe {
        (0..finder_apps.count())
            .filter_map(|i| {
                let app = finder_apps.objectAtIndex(i);
                let pid = app.processIdentifier();
                (pid > 0 && !app.isTerminated()).then_some(pid as i64)
            })
            .collect()
    };

    // NSString 往返（Foundation 对象桥接的最基础验证）
    let ns = NSString::from_str("objc2-safe-binding");
    let roundtrip = ns.to_string();

    // NSURL 构造（nsworkspace.rs file_or_app_url 的简化版）
    let url = unsafe { NSURL::fileURLWithPath(&NSString::from_str("/Applications")) };
    let url_str = unsafe { url.path() }.map(|p| p.to_string());

    let ok = !finder_pids.is_empty() && roundtrip == "objc2-safe-binding";
    json!({
        "ok": ok,
        "ws_desc": ws_desc,
        "finder_pids": finder_pids,
        "string_roundtrip": roundtrip,
        "file_url_path": url_str,
    })
}
