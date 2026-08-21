#!/bin/bash
# esmvp-swift 内核授权缓存（--cache）语义验证，对齐 ObjC 版 test-cache.sh。用法: sudo ./test-cache.sh
# 原理：watch 目录隔离保证只有本脚本产生的 open 会被投递，事件计数即缓存行为的直接证据。
set -uo pipefail
cd "$(dirname "$0")/.."   # 项目根（es-mvp/swift/）

BIN=./ESMvpSwift.app/Contents/MacOS/esmvp-swift
D=/private/tmp/esmvp-swift-cache
rm -rf "${D}"; mkdir -p "${D}"
echo v1 > "${D}/a.txt"
printf '\211PNG\r\n\032\n' > "${D}/deny.png"

EPID=""
start() { "${BIN}" --watch "${D}" --stats-interval 60 --verbose "$@" > /tmp/esmvp-swift-cache.log 2>&1 & EPID=$!; sleep 2; }
stop() { kill -TERM ${EPID} 2>/dev/null; wait ${EPID} 2>/dev/null; }
events() { grep -c 'decision=' /tmp/esmvp-swift-cache.log; }

echo "===== Case A: 基线（无 --cache），3 次新进程 cat a.txt ====="
start
for i in 1 2 3; do cat "${D}/a.txt" > /dev/null; done
sleep 1; stop
echo "事件数=$(events)（期望 3）"
echo

echo "===== Case B: --cache，3 次新进程 cat a.txt ====="
start --cache
for i in 1 2 3; do cat "${D}/a.txt" > /dev/null; done
sleep 1; stop
echo "事件数=$(events)（期望 1：缓存跨进程实例生效）"
echo

echo "===== Case C: --cache，修改文件后再 open（失效验证）====="
start --cache
cat "${D}/a.txt" > /dev/null
echo v2 >> "${D}/a.txt"
cat "${D}/a.txt" > /dev/null
sleep 1; stop
echo "事件数=$(events)（期望 3：写 open 计 1 条 + 修改后缓存失效重新上送）"
echo

echo "===== Case D: --cache，同一进程多次 open + 另一进程再 open ====="
if command -v python3 > /dev/null; then
  start --cache
  python3 -c "[open('${D}/a.txt','rb').read() for _ in range(3)]"
  python3 -c "open('${D}/a.txt','rb').read()"
  sleep 1; stop
  echo "事件数=$(events)（期望 1：同一可执行文件共享缓存）"
else
  echo "无 python3，跳过"
fi
echo

echo "===== Case E: --cache，DENY 不缓存验证 ====="
start --cache
cat "${D}/deny.png" 2>&1 | head -1
cat "${D}/deny.png" 2>&1 | head -1
sleep 1; stop
echo "事件数=$(events)（期望 2：DENY 永不缓存）"
echo

echo "===== Case F: --cache，不同可执行文件 open 同一文件 ====="
if command -v python3 > /dev/null; then
  start --cache
  cat "${D}/a.txt" > /dev/null
  python3 -c "open('${D}/a.txt','rb').read()"
  sleep 1; stop
  echo "事件数=$(events)（期望 2：缓存键含可执行文件维度，ALLOW 不跨进程外溢）"
else
  echo "无 python3，跳过"
fi
