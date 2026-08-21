//! Layer 2 — raw objc_msgSend（objc2::msg_send! 宏）
//!
//! 对应 platform-macos cursor/overlay.rs run_appkit 与 permissions/panel.rs：
//! 对绑定未覆盖的 API 直接用 `class!(类名)` + `msg_send![receiver, selector]`，
//! 返回值类型必须与 ObjC 类型编码严格一致（NSRect 是结构体返回值，
//! setActivationPolicy: 返回 BOOL 不是 void——注释里专门踩过）。
//!
//! 同时演示 skylight.rs 的动态类查找模式：objc_getClass 等价物
//! `AnyClass::get("NSWorkspace")`，运行时按名字解析类再发消息。

use objc2::runtime::{AnyClass, AnyObject};
use objc2::{class, msg_send};
use objc2_foundation::{MainThreadMarker, NSRect};
use serde_json::json;

pub fn run() -> serde_json::Value {
    let _mtm = MainThreadMarker::new().expect("Layer2 必须在主线程运行");

    // ── 静态类名宏（编译期展开为 objc_getClass）──
    // 与 overlay.rs 完全一致：mainScreen 返回类型是 `id`（可空），frame
    // 是结构体返回值 NSRect，backingScaleFactor 是 CGFloat(f64)。
    let main_screen: *mut AnyObject = unsafe { msg_send![class!(NSScreen), mainScreen] };
    let screen_ok = !main_screen.is_null();
    let (w, h, scale) = if screen_ok {
        let frame: NSRect = unsafe { msg_send![main_screen, frame] };
        let scale: f64 = unsafe { msg_send![main_screen, backingScaleFactor] };
        (frame.size.width, frame.size.height, scale)
    } else {
        (0.0, 0.0, 0.0)
    };

    // ── 运行时动态类查找（skylight.rs objc_class 模式的 objc2 版本）──
    // AnyClass::get 在已加载镜像的 __objc_classlist 中按名字找类；
    // 对 dlopen 进来的 Swift dylib 类同样有效（Layer 5 会复用）。
    let ws_class = AnyClass::get("NSWorkspace")
        .ok_or("NSWorkspace class not found")
        .expect("Foundation/AppKit 已链接");
    let ws: *mut AnyObject = unsafe { msg_send![ws_class, sharedWorkspace] };
    let dyn_lookup_ok = !ws.is_null();

    // NSString 动态调用：stringWithUTF8String: 返回 +1 对象，需 retain 管理
    let cstr = b"raw msg_send\0";
    let ns_str: *mut AnyObject =
        unsafe { msg_send![class!(NSString), stringWithUTF8String: cstr.as_ptr()] };
    let dyn_string = if ns_str.is_null() {
        None
    } else {
        // SAFETY: 返回值是 +1 对象，retain 接管所有权（NSString: Message）
        unsafe { objc2::rc::Retained::retain(ns_str.cast::<objc2_foundation::NSString>()) }
            .map(|s| s.to_string())
    };

    let ok = screen_ok && dyn_lookup_ok && dyn_string.as_deref() == Some("raw msg_send");
    json!({
        "ok": ok,
        "screen_points": { "width": w, "height": h, "scale": scale },
        "dynamic_class_lookup": "NSWorkspace",
        "dyn_lookup_ok": dyn_lookup_ok,
        "dyn_string": dyn_string,
    })
}
