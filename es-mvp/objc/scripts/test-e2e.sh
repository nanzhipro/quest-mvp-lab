#!/bin/bash
# esmvp 本机验证脚本（对应 SPEC.md §6 DoD 用例）。用法：sudo ./test.sh
# 注意：macOS 自带 bash 3.2 会把 "$T）" 这类"变量名+全角字符"误解析，全文统一用 ${T} 花括号形式。
set -uo pipefail
cd "$(dirname "$0")/.."   # 项目根（es-mvp/objc/）

BIN=./ESMvp.app/Contents/MacOS/esmvp
T=/private/tmp/esmvp-test
O=/private/tmp/esmvp-other
rm -rf "${T}" "${O}"; mkdir -p "${T}/sub" "${O}"
printf '\211PNG\r\n\032\n' > "${T}/a.png"
printf '\377\330\377\340' > "${T}/b.jpg"
echo hello > "${T}/c.txt"
printf '\211PNG\r\n\032\n' > "${T}/sub/d.png"
printf '\211PNG\r\n\032\n' > "${O}/x.png"

echo "===== Case 1: mute-all 模式（无 --watch）====="
"${BIN}" --stats-interval 3 > /tmp/esmvp-case1.log 2>&1 &
P=$!; sleep 2
cat "${T}/c.txt" > /dev/null && echo "[ok] c.txt 可正常打开（全静音=自动放行）"
cat "${T}/a.png" > /dev/null && echo "[ok] a.png 可正常打开（全静音=自动放行）"
sleep 4; kill -TERM ${P} 2>/dev/null; wait ${P} 2>/dev/null
grep -E "mode=|stats" /tmp/esmvp-case1.log
echo "期望：mode=mute-all，final 统计 received=0（或接近 0）"
echo

echo "===== Case 2: watch 模式（--watch ${T}）====="
"${BIN}" --watch "${T}" --stats-interval 3 --verbose > /tmp/esmvp-case2.log 2>&1 &
P=$!; sleep 2
echo "--- cat a.png （期望 Operation not permitted）:"; cat "${T}/a.png" 2>&1 | head -1
echo "--- cat b.jpg （期望 Operation not permitted）:"; cat "${T}/b.jpg" 2>&1 | head -1
echo "--- cat c.txt （期望 hello）:"; cat "${T}/c.txt"
echo "--- cat sub/d.png （期望 Operation not permitted，前缀含子目录）:"; cat "${T}/sub/d.png" 2>&1 | head -1
echo "--- cat ${O}/x.png （目录外，期望正常打开且 esmvp 无事件日志）:"; head -c4 "${O}/x.png" > /dev/null && echo "(opened ok)"
sleep 4; kill -TERM ${P} 2>/dev/null; wait ${P} 2>/dev/null
echo "--- esmvp 日志:"; cat /tmp/esmvp-case2.log
echo
echo "===== 判定 ====="
echo "1) case1 final received 应为 0；2) case2 日志应只有 4 条事件（a.png/b.jpg/c.txt/sub/d.png），"
echo "   且 png/jpg 为 DENY、c.txt 为 ALLOW；3) 目录外 x.png 不应出现在日志中。"
