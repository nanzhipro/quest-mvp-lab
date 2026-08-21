#!/bin/bash
# L3 内核授权缓存（cache=true）语义验证。用法: sudo ./test-cache.sh
# 原理：watch 目录隔离保证只有本脚本产生的 open 会被投递，事件计数即缓存行为的直接证据。
# 注意：内核授权缓存全局共享，且 es_new_client 创建时自动清空——每个 Case 重启 esmvp 即干净基线。
set -uo pipefail
cd "$(dirname "$0")/.."   # 项目根（es-mvp/objc/）

BIN=./ESMvp.app/Contents/MacOS/esmvp
D=/private/tmp/esmvp-cache
rm -rf "${D}"; mkdir -p "${D}"
echo v1 > "${D}/a.txt"
printf '\211PNG\r\n\032\n' > "${D}/deny.png"

EPID=""
start() { "${BIN}" --watch "${D}" --stats-interval 60 --verbose "$@" > /tmp/esmvp-cache.log 2>&1 & EPID=$!; sleep 2; }
stop() { kill -TERM ${EPID} 2>/dev/null; wait ${EPID} 2>/dev/null; }
events() { grep -c '\[esmvp\]\[event\]' /tmp/esmvp-cache.log; }

echo "===== Case A: 基线（无 --cache），3 次新进程 cat a.txt ====="
start
for i in 1 2 3; do cat "${D}/a.txt" > /dev/null; done
sleep 1; stop
echo "事件数=$(events)（期望 3：无缓存，每次 open 都上送）"
echo

echo "===== Case B: --cache，3 次新进程 cat a.txt ====="
start --cache
for i in 1 2 3; do cat "${D}/a.txt" > /dev/null; done
sleep 1; stop
echo "事件数=$(events)（=1 → 缓存跨进程实例生效；=3 → 按进程实例缓存，每次新 cat 都 miss）"
echo

echo "===== Case C: --cache，修改文件后再 open（失效验证）====="
start --cache
cat "${D}/a.txt" > /dev/null
echo v2 >> "${D}/a.txt"   # 这是一次写 open（本身计 1 条事件），随后内核应使该文件缓存条目失效
cat "${D}/a.txt" > /dev/null
sleep 1; stop
echo "事件数=$(events)（=3 → 修改后缓存失效，第三次 open 重新上送；=2 → 未失效）"
echo

echo "===== Case D: --cache，同一进程多次 open + 另一进程再 open（缓存键维度）====="
if command -v python3 > /dev/null; then
  start --cache
  python3 -c "[open('${D}/a.txt','rb').read() for _ in range(3)]"
  python3 -c "open('${D}/a.txt','rb').read()"
  sleep 1; stop
  echo "事件数=$(events)（=1 → 按可执行文件维度缓存；=2 → 按进程实例缓存；=4 → 缓存未生效）"
else
  echo "无 python3，跳过"
fi
echo

echo "===== Case E: --cache，DENY 不缓存验证 ====="
start --cache
cat "${D}/deny.png" 2>&1 | head -1
cat "${D}/deny.png" 2>&1 | head -1
sleep 1; stop
echo "事件数=$(events)（期望 2：DENY 永不缓存，每次拦截都上送且都被拒）"
echo

echo "===== Case F: --cache，不同可执行文件 open 同一文件（缓存键最后一格）====="
if command -v python3 > /dev/null; then
  start --cache
  cat "${D}/a.txt" > /dev/null        # 用 cat 建立缓存条目
  python3 -c "open('${D}/a.txt','rb').read()"   # 换 python3 开同一文件
  sleep 1; stop
  echo "事件数=$(events)（=2 → 缓存键含可执行文件维度；=1 → 只按文件缓存，任何进程共享 ALLOW）"
else
  echo "无 python3，跳过"
fi
