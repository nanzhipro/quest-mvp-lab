//! Layer 5 — Rust → Swift：dlopen 加载 Swift 编译的 dylib，三种入口全打通
//!
//! 这是本 MVP 的核心验证：Swift 代码（内部还调用了 AppKit 的 NSScreen）
//! 被 Rust 用与 platform-macos 驱动 SkyLight 私有框架完全相同的手法调用：
//!
//! 1. `dlopen` 加载 dylib（skylight.rs ensure_skylight_loaded 的镜像）；
//! 2. `dlsym` 解析 @_cdecl 导出的 C 符号（find_sym 的镜像）；
//! 3. `objc_getClass` 按名字找 @objc 类（objc_class 的镜像，objc2 里是
//!    AnyClass::get），再用 msg_send! 驱动；
//! 4. block2::RcBlock 作为 Swift @convention(block) 参数传进去，Swift 回调
//!    Rust —— 一次完整的 Rust → Swift → Rust 往返。
//!
//! Swift 类方法内部调用 NSScreen（AppKit），所以链路是
//! Rust → ObjC runtime → Swift → AppKit → Swift → Rust。

use std::ffi::{c_char, c_void, CStr};
use std::path::PathBuf;
use std::sync::OnceLock;

use block2::RcBlock;
use objc2::msg_send;
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2_foundation::NSString;
use serde_json::json;

// ── dylib 路径解析：环境变量优先，默认取仓库内 swift-lib/ ──
fn dylib_path() -> PathBuf {
    if let Ok(p) = std::env::var("SWIFT_BRIDGE_DYLIB") {
        return PathBuf::from(p);
    }
    // CARGO_MANIFEST_DIR 编译期注入：rust/../swift-lib/libSwiftBridge.dylib
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust crate 上一级是项目根")
        .join("swift-lib/libSwiftBridge.dylib")
}

fn ensure_loaded() {
    // 句柄存 usize：OnceLock 要求 Send+Sync，裸指针不满足；usize 无损往返
    static HANDLE: OnceLock<usize> = OnceLock::new();
    HANDLE.get_or_init(|| {
        let path = dylib_path();
        let cpath =
            std::ffi::CString::new(path.to_str().expect("路径非 UTF-8")).expect("路径含 NUL");
        // SAFETY: dlopen 标准用法；RTLD_GLOBAL 让 dylib 的 ObjC 类注册进全局 runtime
        let handle = unsafe { libc::dlopen(cpath.as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL) };
        assert!(
            !handle.is_null(),
            "dlopen 失败: {}",
            unsafe { std::ffi::CStr::from_ptr(libc::dlerror()) }.to_string_lossy()
        );
        handle as usize
    });
}

/// dlsym 解析 + transmute 为函数指针（skylight.rs find_sym + as_fn 的镜像）
fn find_sym(name: &[u8]) -> Option<*mut c_void> {
    ensure_loaded();
    // SAFETY: dlsym 标准用法；name 以 NUL 结尾
    let ptr = unsafe { libc::dlsym(libc::RTLD_DEFAULT, name.as_ptr() as *const c_char) };
    (!ptr.is_null()).then_some(ptr)
}

type AddFn = unsafe extern "C" fn(i64, i64) -> i64;
type GreetingFn = unsafe extern "C" fn() -> *const c_char;

fn call_cdecl() -> serde_json::Value {
    // ── @_cdecl 符号：swift_bridge_add / swift_bridge_greeting ──
    let add: AddFn = unsafe {
        std::mem::transmute_copy(&find_sym(b"swift_bridge_add\0").expect("add 符号缺失"))
    };
    let greeting: GreetingFn = unsafe {
        std::mem::transmute_copy(&find_sym(b"swift_bridge_greeting\0").expect("greeting 符号缺失"))
    };

    // SAFETY: 两个函数都是纯 Swift 实现，无指针参数/返回生命周期问题
    let sum = unsafe { add(20, 22) };
    let text = unsafe {
        let p = greeting();
        CStr::from_ptr(p).to_string_lossy().into_owned()
    };
    json!({ "swift_bridge_add(20,22)": sum, "swift_bridge_greeting": text })
}

fn call_objc_class() -> serde_json::Value {
    // ── @objc 类：objc_getClass + msg_send（skylight.rs 驱动私有类的方式）──
    let cls = objc2::runtime::AnyClass::get("SwiftBridgeService")
        .expect("dlopen 后类应已注册进 ObjC runtime");
    let svc: *mut AnyObject = unsafe { msg_send![cls, new] };
    assert!(!svc.is_null(), "SwiftBridgeService 实例化失败");

    // 1) 纯计算：add:to:
    let sum: i64 = unsafe { msg_send![svc, add: 20 to: 22] };

    // 2) 字符串：upper:（NSString 进出 runtime）
    let input = NSString::from_str("bridge");
    let ret: *mut AnyObject = unsafe { msg_send![svc, upper: &*input] };
    // SAFETY: ret 是 +1 对象（方法名 non-ARC 约定），retain 接管所有权
    let upper = unsafe { Retained::<NSString>::retain(ret.cast::<NSString>()) }
        .expect("upper: 应返回 NSString")
        .to_string();

    // 3) Swift → AppKit：mainScreenDescription（内部调 NSScreen.main）
    let ret2: *mut AnyObject = unsafe { msg_send![svc, mainScreenDescription] };
    let screen_desc = unsafe { Retained::<NSString>::retain(ret2.cast::<NSString>()) }
        .expect("mainScreenDescription 应返回 NSString")
        .to_string();

    // 4) Block 往返：computeAsync:completion: —— Swift 回调 Rust
    //    &*block 解引用到 Block<F>（RefEncode 实现于 Block 而非 RcBlock）
    let block = RcBlock::new(|v: i64| {
        // 同步回调，直接打印标记（由主调用方读取）
        BLOCK_RESULT.with(|c| c.set(v));
    });
    unsafe {
        let _: () = msg_send![svc, computeAsync: 21_i64 completion: &*block];
    }
    let roundtrip = BLOCK_RESULT.with(|c| c.get());

    // 显式 release（+1 对象；真实代码用 Retained 管理）
    unsafe {
        let _: () = msg_send![svc, release];
    }

    json!({
        "add(20,22)": sum,
        "upper(bridge)": upper,
        "swift->appkit": screen_desc,
        "block_roundtrip": roundtrip,
    })
}

thread_local! {
    static BLOCK_RESULT: std::cell::Cell<i64> = const { std::cell::Cell::new(-1) };
}

pub fn run() -> serde_json::Value {
    let cdecl = call_cdecl();
    let objc = call_objc_class();
    let ok = cdecl["swift_bridge_add(20,22)"] == 42
        && objc["add(20,22)"] == 42
        && objc["upper(bridge)"] == "BRIDGE"
        && objc["block_roundtrip"] == 42;
    json!({ "ok": ok, "cdecl": cdecl, "objc_class": objc })
}
