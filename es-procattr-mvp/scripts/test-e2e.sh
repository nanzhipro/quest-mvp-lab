#!/bin/bash
# es-procattr-mvp 真机 DoD 用例验证（需 root）。
# 用例：
#   Case 1 种子发现：启动后枚举 roots 命中的进程并打印其进程链（chain）。
#   Case 2 实时 exec 归因：制造一次受控子进程（bash），日志出现 exec 事件且 parent/chain 正确。
#   Case 3 行为链：受控进程 create/write/rename/unlink 文件，日志归因 pid 正确。
#
# 依赖：先 `make package` 产出签名 App。运行方式：
#   sudo ./scripts/test-e2e.sh [config.json]
set -euo pipefail
cd "$(dirname "$0")/.."

APP="./ESProcattr.app/Contents/MacOS/es-procattr"
CONFIG="${1:-./config.example.json}"
OUT="$(mktemp -d)/procattr-e2e.jsonl"
echo "=== e2e config=$CONFIG out=$OUT ==="

# 后台启动 daemon（由本脚本同一 shell 派生，SIGINT 收尾）
"$APP" "$CONFIG" --output "$OUT" &
DAEMON_PID=$!
trap 'kill -INT "$DAEMON_PID" 2>/dev/null || true' EXIT
sleep 2

# 制造一条受控进程链 + 文件行为
bash -c 'echo procattr-e2e > /tmp/procattr-e2e-marker.txt; mv /tmp/procattr-e2e-marker.txt /tmp/procattr-e2e-marker2.txt; rm -f /tmp/procattr-e2e-marker2.txt'
sleep 4

kill -INT "$DAEMON_PID" 2>/dev/null || true
wait "$DAEMON_PID" 2>/dev/null || true

echo "=== 关键事件（grep） ==="
grep -E "root|exec|write|create|rename|unlink" "$OUT" 2>/dev/null | grep -E "procattr-e2e|chain" | head -30 || echo "(无匹配，请检查日志)"
echo "=== 完成，完整 JSONL 在 $OUT ==="
