#!/bin/bash
# es-process-mvp 真机 DoD 用例验证。用法：sudo ./scripts/test-e2e.sh
#
# 覆盖策略匹配语义（bundleId 命中 → 仅 PDF 的 AUTH_OPEN 被 DENY）：
#   Case 1（控制 Preview）：PDF 打开被 DENY，TXT 打开正常 ALLOW（确定性验证）。
#   Case 2（控制 WeChat）：非 PDF 打开全部放行，WeChat 正常运行（证明从"全 DENY"改为"仅 PDF"）。
#   Case 3（未命中）：默认 ALLOW——Preview 打开 PDF 不被拦截。
#
# 前置：已完成签名打包（make package）+ 系统设置对 ESProcessMvp.app 授予完全磁盘访问权限。
# 注意：macOS 自带 bash 3.2 会把 "${T}）" 这类"变量名+全角字符"误解析，统一用 ${T} 花括号形式。
set -uo pipefail
cd "$(dirname "$0")/.."   # 项目根

BIN=./ESProcessMvp.app/Contents/MacOS/es-process-mvp
USER="${SUDO_USER:-$(stat -f '%Su' /dev/console)}"
DIR=/tmp/esproc-e2e
PDF="$DIR/esproc-test.pdf"
TXT="$DIR/esproc-test.txt"
C1=/tmp/esproc-e2e-preview.yaml
C2=/tmp/esproc-e2e-wechat.yaml
C3=/tmp/esproc-e2e-miss.yaml
L1=/tmp/esproc-e2e-preview.log
L2=/tmp/esproc-e2e-wechat.log
L3=/tmp/esproc-e2e-miss.log

say()  { printf '\n===== %s =====\n' "$1"; }
pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; FAILED=1; }

quit_app() {
  local app="$1"
  sudo -u "$USER" osascript -e "quit app \"$app\"" 2>/dev/null || true
  sleep 2
  pkill -x "$app" 2>/dev/null || true
  sleep 1
}

FAILED=0
mkdir -p "$DIR"
printf '%%PDF-1.4 test\n' > "$PDF"
printf 'hello txt\n' > "$TXT"

printf 'bundleIds:\n  - com.apple.Preview\n' > "$C1"
printf 'bundleIds:\n  - com.tencent.xinWeChat\n' > "$C2"
printf 'bundleIds:\n  - com.example.nothing\n' > "$C3"

say "Case 1: 控制 Preview → PDF 打开 DENY，TXT 打开 ALLOW"
quit_app Preview
rm -f "$L1"
"$BIN" "$C1" > "$L1" 2>&1 &
DPID=$!
sleep 2
echo "--- 打开 PDF（期望 DENY） ---"
sudo -u "$USER" open -a Preview "$PDF" 2>/dev/null || true
sleep 2
echo "--- 打开 TXT（期望 ALLOW） ---"
sudo -u "$USER" open -a Preview "$TXT" 2>/dev/null || true
sleep 2
kill -TERM "$DPID" 2>/dev/null
wait "$DPID" 2>/dev/null

echo "--- 判定 ---"
grep -q "decision=DENY" "$L1" && grep -q "esproc-test.pdf" "$L1" \
  && pass "PDF 打开被 DENY" || fail "未记录 PDF DENY"
grep -q "esproc-test.txt" "$L1" \
  && fail "TXT 打开被 DENY（应 ALLOW）" || pass "TXT 打开未 DENY（ALLOW 生效）"
grep -q "controlled=1" "$L1" && pass "Preview 命中策略并 watch" || fail "未 watch Preview"
echo "--- Case 1 关键日志 ---"
grep -E "decision=DENY|stats kind=final" "$L1" | head -6

say "Case 2: 控制 WeChat → 非 PDF 打开全部放行，WeChat 正常运行"
quit_app WeChat
rm -f "$L2"
"$BIN" "$C2" > "$L2" 2>&1 &
DPID=$!
sleep 2
sudo -u "$USER" open -a WeChat 2>/dev/null || true
sleep 6
kill -TERM "$DPID" 2>/dev/null
wait "$DPID" 2>/dev/null

echo "--- 判定 ---"
if pgrep -x WeChat > /dev/null; then
  pass "WeChat 正常运行（非 PDF 打开未受影响）"
else
  fail "WeChat 未能启动"
fi
echo "--- Case 2 关键日志 ---"
grep -E "exec .*watched=true|stats kind=final" "$L2" | head -4

say "Case 3: 未命中 → 默认 ALLOW，Preview 打开 PDF 不被拦截"
quit_app Preview
rm -f "$L3"
"$BIN" "$C3" > "$L3" 2>&1 &
DPID=$!
sleep 2
sudo -u "$USER" open -a Preview "$PDF" 2>/dev/null || true
sleep 2
kill -TERM "$DPID" 2>/dev/null
wait "$DPID" 2>/dev/null

echo "--- 判定 ---"
grep -q "decision=DENY" "$L3" \
  && fail "默认 ALLOW 下出现 DENY（策略误伤）" || pass "未管控进程 PDF 打开零 DENY（默认 ALLOW）"

say "收尾：恢复 WeChat（正常启动，无 daemon）"
sudo -u "$USER" open -a WeChat 2>/dev/null || true
sleep 2

rm -rf "$DIR" "$C1" "$C2" "$C3"
if [ "$FAILED" -ne 0 ]; then
  echo ""
  echo "RESULT: FAILED"
  exit 1
fi
echo ""
echo "RESULT: ALL PASS"
