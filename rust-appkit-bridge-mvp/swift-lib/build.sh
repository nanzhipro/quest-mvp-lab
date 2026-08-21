#!/bin/bash
# 编译 SwiftBridge.dylib（Rust → Swift 方向的核心产物）
set -euo pipefail
cd "$(dirname "$0")"

swiftc -emit-library \
    -O \
    -module-name SwiftBridge \
    -o libSwiftBridge.dylib \
    SwiftBridge.swift \
    -framework Foundation \
    -framework AppKit

echo "built: $(pwd)/libSwiftBridge.dylib"
otool -L libSwiftBridge.dylib | head -8
