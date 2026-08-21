fn main() {
    // es_message_t 布局大且随 message version 演进，字段提取收敛到 C shim（见 csrc/）。
    // blocks 语法（es_handler_block_t）要求 -fblocks。
    cc::Build::new()
        .file("csrc/es_shim.c")
        .flag("-fblocks")
        .compile("es_shim");

    // EndpointSecurity 以动态库（非 framework）形式存在于 SDK usr/lib。
    println!("cargo:rustc-link-lib=dylib=EndpointSecurity");
    println!("cargo:rerun-if-changed=csrc/es_shim.c");
    println!("cargo:rerun-if-changed=csrc/es_shim.h");
}
