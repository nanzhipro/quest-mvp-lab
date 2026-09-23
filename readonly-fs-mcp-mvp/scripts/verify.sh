#!/usr/bin/env bash
#
# 一键验证 readonly-fs-mcp：格式、lint、全量测试、行覆盖率门槛（95%），
# 证据落盘到 runs/<时间戳>/verify.log（runs/ 不入库，可随时重建）。
#
# 用法：./scripts/verify.sh [覆盖率门槛，默认 95]
set -euo pipefail

cd "$(dirname "$0")/.."
threshold="${1:-95}"
run_dir="runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$run_dir"

if ! command -v cargo-llvm-cov >/dev/null 2>&1; then
    echo "缺少 cargo-llvm-cov：cargo install cargo-llvm-cov（并 rustup component add llvm-tools-preview）" >&2
    exit 2
fi

{
    echo "== cargo fmt --all --check"
    cargo fmt --all --check
    echo "== cargo clippy --all-targets -- -D warnings"
    cargo clippy --all-targets -- -D warnings
    echo "== cargo test --all-targets"
    cargo test --all-targets
    echo "== cargo llvm-cov（行覆盖率 >= ${threshold}%）"
    cargo llvm-cov --all-targets --fail-under-lines "$threshold" --summary-only
    echo "== 完成：以上全部通过"
} 2>&1 | tee "$run_dir/verify.log"

echo "验证日志：$run_dir/verify.log"
