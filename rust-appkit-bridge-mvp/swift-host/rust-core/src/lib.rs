//! rust-core — Swift 宿主调用的 Rust 静态库（Swift → Rust 方向）
//!
//! 手法与 cua-driver 的 cua_driver_abi.h 完全一致：
//! `#[no_mangle] pub extern "C"` 导出稳定 ABI，宿主语言经 C 头文件调用。
//! 函数指针回调则证明 Rust 能反向调用 Swift 传入的代码。

use std::ffi::{c_char, CString};

/// 纯计算：i64 相加
#[no_mangle]
pub extern "C" fn rust_core_add(a: i64, b: i64) -> i64 {
    a + b
}

/// 返回堆分配的 C 字符串（调用方必须用 rust_core_free_string 释放）
#[no_mangle]
pub extern "C" fn rust_core_greeting() -> *mut c_char {
    CString::new("hello from Rust core (staticlib, C ABI)")
        .expect("静态字符串不含 NUL")
        .into_raw()
}

/// 释放 rust_core_greeting 分配的内存（所有权跨语言边界的手工管理）
#[no_mangle]
pub unsafe extern "C" fn rust_core_free_string(p: *mut c_char) {
    if !p.is_null() {
        // SAFETY: p 必须是 rust_core_greeting 的返回值（契约文档写明）
        drop(CString::from_raw(p));
    }
}

/// 函数指针回调：Rust 调用 Swift 传入的 C 函数
#[no_mangle]
pub extern "C" fn rust_core_apply(x: i64, cb: Option<extern "C" fn(i64) -> i64>) -> i64 {
    match cb {
        Some(f) => f(x * 3),
        None => -1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn add_is_commutative() {
        assert_eq!(rust_core_add(40, 2), 42);
        assert_eq!(rust_core_add(2, 40), 42);
    }

    #[test]
    fn greeting_roundtrips_through_free() {
        let p = rust_core_greeting();
        assert!(!p.is_null());
        let s = unsafe { std::ffi::CStr::from_ptr(p) }.to_string_lossy();
        assert!(s.contains("Rust core"));
        unsafe { rust_core_free_string(p) };
    }

    #[test]
    fn apply_calls_back_and_handles_null() {
        extern "C" fn double(x: i64) -> i64 {
            x * 2
        }
        assert_eq!(rust_core_apply(7, Some(double)), 42);
        assert_eq!(rust_core_apply(7, None), -1);
    }
}
