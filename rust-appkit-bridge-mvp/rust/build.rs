//! build.rs — 镜像 platform-macos/build.rs 的链接配置。
//!
//! objc2-app-kit / objc2-foundation 的 extern block 是“空的”，不会把
//! AppKit / Foundation 的链接指令传播进 Cargo 依赖图，必须在这里显式
//! 声明（platform-macos build.rs 注释原文说明）。libdispatch 需要 SDK
//! 的 usr/lib/system 搜索路径，同理照搬。

fn main() {
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os != "macos" {
        return;
    }

    // AppKit / Foundation / QuartzCore / CoreGraphics 显式链接
    println!("cargo:rustc-link-lib=framework=AppKit");
    println!("cargo:rustc-link-lib=framework=Foundation");
    println!("cargo:rustc-link-lib=framework=QuartzCore");
    println!("cargo:rustc-link-lib=framework=CoreGraphics");

    // Security / ApplicationServices / CoreFoundation 由源码内 #[link] 声明，
    // 这里再补一次确保稳定（与 platform-macos 一致）。
    println!("cargo:rustc-link-lib=framework=Security");
    println!("cargo:rustc-link-lib=framework=ApplicationServices");
    println!("cargo:rustc-link-lib=framework=CoreFoundation");

    // SDK 子库搜索路径：让 #[link(name = "dispatch", kind = "dylib")]
    // 能找到 libdispatch.tbd。
    let sdk_root = std::env::var("SDKROOT").unwrap_or_else(|_| {
        std::process::Command::new("xcrun")
            .args(["--sdk", "macosx", "--show-sdk-path"])
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
            .unwrap_or_default()
            .trim()
            .to_owned()
    });
    if !sdk_root.is_empty() {
        println!("cargo:rustc-link-search={sdk_root}/usr/lib/system");
        println!("cargo:rustc-link-search={sdk_root}/usr/lib");
        println!("cargo:rustc-link-search=framework={sdk_root}/System/Library/Frameworks");
    }
}
