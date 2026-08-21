//! Layer 6 — 主线程分发（libdispatch：dispatch_async_f + 主 RunLoop 泵）
//!
//! AppKit 必须跑在主线程。platform-macos 的架构是：tokio/MCP 服务器在后台
//! 线程，UI 更新通过全局 channel + dispatch_async_f 投递到主队列
//! （cursor/overlay.rs、pip/mod.rs 完全一致）。本层用最小形态复刻：
//!
//! - 后台线程 `dispatch_async_f(main_queue, Box 载荷, C 回调)`；
//! - 主线程循环 `CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, false)`
//!   泵主 RunLoop 让队列里的 block 真正执行（CLI 没有 NSApplication.run()，
//!   必须手动泵；真实 App 由 NSApp.run() 完成这一步）。
//!
//! `_dispatch_main_q` 是 libdispatch 导出符号，与 pip/mod.rs 相同手法
//! （dispatch_get_main_queue() 是内联函数，取其背后的符号最稳）。

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::json;

#[link(name = "dispatch", kind = "dylib")]
extern "C" {
    static _dispatch_main_q: u8;
    fn dispatch_async_f(
        queue: *const c_void,
        context: *mut c_void,
        work: unsafe extern "C" fn(*mut c_void),
    );
}

// kCFRunLoopDefaultMode 是 CoreFoundation 导出的数据符号（CFStringRef 常量）
#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    static kCFRunLoopDefaultMode: *const c_void;
    fn CFRunLoopRunInMode(
        mode: *const c_void,
        seconds: f64,
        return_after_source_handled: bool,
    ) -> i32;
}

/// 投递到主队列的载荷：值 + 完成标记（由主线程回调验证载荷完整性）
struct MainQueuePayload {
    value: i64,
    done: Arc<AtomicBool>,
    delivered_value: Arc<AtomicI64>,
}

/// 在主线程执行的 C 回调：回收 Box，写完成标记与载荷值
unsafe extern "C" fn on_main_queue(context: *mut c_void) {
    let payload = Box::from_raw(context as *mut MainQueuePayload);
    payload
        .delivered_value
        .store(payload.value, Ordering::SeqCst);
    payload.done.store(true, Ordering::SeqCst);
    // payload 在函数末尾 drop（Box 所有权在主线程收回）
}

pub fn run() -> serde_json::Value {
    let done = Arc::new(AtomicBool::new(false));
    let delivered_value = Arc::new(AtomicI64::new(-1));
    let payload = Box::new(MainQueuePayload {
        value: 42,
        done: done.clone(),
        delivered_value: delivered_value.clone(),
    });

    // 后台线程投递（模拟 tokio worker）
    let handle = std::thread::spawn(|| unsafe {
        let main_queue = &raw const _dispatch_main_q as *const c_void;
        dispatch_async_f(
            main_queue,
            Box::into_raw(payload) as *mut c_void,
            on_main_queue,
        );
    });
    handle.join().expect("投递线程未结束");

    // 主线程泵 RunLoop，直到标记置位（5s 超时兜底）
    let deadline = Instant::now() + Duration::from_secs(5);
    while !done.load(Ordering::SeqCst) && Instant::now() < deadline {
        unsafe {
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, false);
        }
    }

    let delivered = done.load(Ordering::SeqCst);
    let delivered_value = delivered_value.load(Ordering::SeqCst);
    json!({
        "ok": delivered && delivered_value == 42,
        "main_queue_dispatched": delivered,
        "delivered_value": delivered_value,
        "timeout_guard": "5s",
    })
}
