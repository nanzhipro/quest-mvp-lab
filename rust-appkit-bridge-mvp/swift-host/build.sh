#!/bin/bash
# 构建 Swift → Rust 方向的完整产物：rust-core staticlib → swiftc 链接
set -euo pipefail
cd "$(dirname "$0")"

echo "==> building rust-core staticlib"
(cd rust-core && cargo build --release)

echo "==> linking swift host"
swiftc -O \
    -import-objc-header rust_core.h \
    main.swift \
    rust-core/target/release/librust_core.a \
    -o rust-host-demo

echo "built: $(pwd)/rust-host-demo"
