#!/usr/bin/env bash
# 编译 shaders/*.metal → Sources/FluidMotionDemo/*.metallib（本机实测环境：arm64）
# 用途：着色器源码变更后重新生成 metallib（metallib 已提交入库，swift build/run 无需本脚本）。
set -euo pipefail
cd "$(dirname "$0")/.."

for src in shaders/*.metal; do
  name="$(basename "$src" .metal)"
  air="$(mktemp -t "$name").air"
  xcrun -sdk macosx metal -c "$src" -o "$air"
  xcrun -sdk macosx metallib "$air" -o "Sources/FluidMotionDemo/$name.metallib"
  rm -f "$air"
  echo "compiled Sources/FluidMotionDemo/$name.metallib"
done
