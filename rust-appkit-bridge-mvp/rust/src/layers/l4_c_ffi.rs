//! Layer 4 — 裸 C FFI（#[link] 系统框架 + core-graphics/core-foundation crate）
//!
//! platform-macos 中这类桥接的三个代表：
//! - session.rs：`#[link(name = "Security", kind = "framework")]` 调 SessionGetInfo
//! - ax/bindings.rs：`#[link(name = "ApplicationServices")]` 调 AXIsProcessTrusted 等
//! - tools/get_screen_size.rs：core-graphics crate 的 CGMainDisplayID/CGDisplayBounds
//!
//! 本层全部只读、不触发 TCC 弹窗，可在任意环境安全运行。

use core_graphics::display::{CGDisplayBounds, CGMainDisplayID};
use core_graphics::event::CGEvent;
use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};
use foreign_types::ForeignType; // CGEvent::as_ptr 来自该 trait
use serde_json::json;

// ── CoreGraphics 私有补充：CGEventSetLocation ──
// core-graphics crate 没有绑定这个 setter，platform-macos 也是手写原生 FFI
// （input/interactive.rs:743 同款声明）。
#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGEventSetLocation(event: *mut std::ffi::c_void, x: f64, y: f64);
}

// ── Security.framework：安全会话探测（session.rs 原样复刻）──
#[link(name = "Security", kind = "framework")]
extern "C" {
    fn SessionGetInfo(session: i32, session_id: *mut i32, attributes: *mut u32) -> i32; // OSStatus
}

const CALLER_SECURITY_SESSION: i32 = -1;
const SESSION_HAS_GRAPHIC_ACCESS: u32 = 0x0010;

fn has_graphic_access() -> bool {
    let mut session_id: i32 = 0;
    let mut attributes: u32 = 0;
    // SAFETY: SessionGetInfo 是纯 C 函数，指针指向合法栈变量
    let status =
        unsafe { SessionGetInfo(CALLER_SECURITY_SESSION, &mut session_id, &mut attributes) };
    status == 0 && (attributes & SESSION_HAS_GRAPHIC_ACCESS) != 0
}

// ── ApplicationServices.framework：AX 信任状态（只读，不弹窗）──
#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    fn AXIsProcessTrusted() -> bool;
}

pub fn run() -> serde_json::Value {
    // 1) Security 会话探测（session.rs #1724 的防护逻辑）
    let graphic_access = has_graphic_access();

    // 2) AX 信任状态：false 表示本进程未被授予辅助功能权限
    let ax_trusted = unsafe { AXIsProcessTrusted() };

    // 3) CG 显示信息（get_screen_size.rs 模式：CG API 线程安全，无需主线程）
    let display_id = unsafe { CGMainDisplayID() };
    let (w, h) = if display_id != 0 {
        let bounds = unsafe { CGDisplayBounds(display_id) };
        (bounds.size.width as i64, bounds.size.height as i64)
    } else {
        (0, 0)
    };

    // 4) CGEvent 构造 + 手工 CGEventSetLocation（input/mouse.rs 模式：
    //    只构造不投递，无需任何权限；setter 走原生 FFI 补充绑定缺口）
    let event_ok = (|| {
        let source = CGEventSource::new(CGEventSourceStateID::HIDSystemState).ok()?;
        let ev =
            CGEvent::new_keyboard_event(source, 0 /* keycode A */, true /* keydown */).ok()?;
        // SAFETY: CGEventSetLocation 是纯 C 函数；event 指针在 ev 生命周期内有效
        unsafe { CGEventSetLocation(ev.as_ptr().cast(), 100.0, 200.0) };
        let loc = ev.location();
        (loc.x == 100.0 && loc.y == 200.0).then_some(())
    })()
    .is_some();

    json!({
        "ok": true,
        "graphic_session_access": graphic_access,
        "ax_trusted": ax_trusted,
        "main_display": { "id": display_id, "width": w, "height": h },
        "cgevent_construct": event_ok,
    })
}
