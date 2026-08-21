#!/bin/bash
# verify.sh — 构建 + 运行两个方向的可执行产物 + 断言关键输出
set -euo pipefail
cd "$(dirname "$0")/.."

echo "════════════════════════════════════════════════════════"
echo "  1/2  Rust → AppKit/ObjC/Swift  (bridge-demo)"
echo "════════════════════════════════════════════════════════"

export SWIFT_BRIDGE_DYLIB="$PWD/swift-lib/libSwiftBridge.dylib"
OUT="$(rust/target/release/bridge-demo)"
echo "$OUT"

PASS=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("demo_pass", False))')
if [ "$PASS" != "True" ]; then
    echo "✗ bridge-demo FAILED"
    exit 1
fi
echo "✓ bridge-demo: demo_pass=True"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  2/2  Swift → Rust (rust-host-demo)"
echo "════════════════════════════════════════════════════════"

OUT2="$(swift-host/rust-host-demo)"
echo "$OUT2"

if ! echo "$OUT2" | grep -q "SWIFT_HOST_PASS"; then
    echo "✗ rust-host-demo FAILED"
    exit 1
fi
echo "✓ rust-host-demo: SWIFT_HOST_PASS"

echo ""
echo "ALL CHECKS PASSED"
