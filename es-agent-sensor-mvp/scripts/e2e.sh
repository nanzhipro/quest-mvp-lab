#!/bin/bash
# es-agent-sensor 端到端验收：假 Agent 场景 + JSONL 断言。
#
# 前置：
#   1. ./scripts/build.sh 已成功（产出已签名的 ESAgentSensor.app）
#   2. 本脚本需 root（ES client 要求）；已签名身份须已授予完全磁盘访问
#      （系统设置 → 隐私与安全性 → 完全磁盘访问，按签名身份记账，重编不重复授权）
#
# 场景：临时目录充当假 Agent 工作目录；现场编译 ad-hoc 签名的最小 C 程序当
# "agent 可执行文件"（executable 命中目录前缀 → actor_process 归因；注意直接
# 拷贝 /bin/bash 会被平台二进制约束 SIGKILL，macOS 26 实测）。假 agent 依次执行：
# 派生子进程 / 增删改 rename/truncate/chmod 文件（目录内外）/ 跨进程 Unix
# socket bind+connect / 保持 4s 的 TCP 连接；另从 agent 外进程触碰目录内文件
# （target_path 归因）。
#
# 停机：传感器用 --duration 自动停机。签名后的 ES client 进程受 macOS 反篡改
# 保护，SIGINT/SIGTERM 无效（macOS 26.5.2 实测），外部只能 SIGKILL。
#
# 用法：sudo ./scripts/e2e.sh [--keep-app]
set -euo pipefail
cd "$(dirname "$0")/.."

APP="ESAgentSensor.app"
BIN="$APP/Contents/MacOS/es-agent-sensor"
KEEP_APP=0
[ "${1:-}" = "--keep-app" ] && KEEP_APP=1

if [ "$(id -u)" -ne 0 ]; then
  echo "需要 root：sudo $0" >&2
  exit 1
fi
if [ ! -x "$BIN" ]; then
  echo "缺少 ${BIN}，请先运行 ./scripts/build.sh" >&2
  exit 1
fi

WORK="$(mktemp -d /tmp/es-sensor-e2e.XXXXXX)"
AGENT_DIR="$WORK/fake-agent"
OUT="$WORK/events.jsonl"
mkdir -p "$AGENT_DIR/bin"

cleanup() {
  # 脚本中途失败时兜底停掉传感器（SIGKILL 是唯一可靠手段），避免残留 root 进程
  if [ -n "${SENSOR:-}" ] && kill -0 "$SENSOR" 2>/dev/null; then
    kill -9 "$SENSOR" 2>/dev/null || true
  fi
  rm -rf "$WORK"
  if [ "$KEEP_APP" -eq 0 ]; then
    rm -rf "$APP"
  fi
  return 0 2>/dev/null || true
}
trap cleanup EXIT

echo "== 启动传感器（agent 目录: ${AGENT_DIR}）"
# 假 agent 行为序列约 8s，外加启动 3s + 收尾 3s，--duration 20s 兜底自动停机。
"$BIN" --agent "fake=$AGENT_DIR" --output "$OUT" \
  --net-poll-interval 1 --stats-interval 0 --log-level info --duration 20 &
SENSOR=$!
sleep 3   # 等 seed 回填 + 订阅生效
if ! kill -0 "$SENSOR" 2>/dev/null; then
  echo "传感器启动失败（未授权 FDA？缺 entitlement？）" >&2
  exit 1
fi

echo "== 编译并执行假 agent 行为序列"
xcrun cc -O1 -o "$AGENT_DIR/bin/agent" scripts/fake_agent.c
"$AGENT_DIR/bin/agent" "$AGENT_DIR"
# target_path 归因：agent 外进程（本脚本 /bin/bash）触碰目录内文件
echo external > "$AGENT_DIR/external-touch.txt"

echo "== 等待传感器自动停机"
wait "$SENSOR" || true

python3 - "$OUT" <<'PYEOF'
import json, sys

out_path = sys.argv[1]
events = []
with open(out_path) as f:
    for i, line in enumerate(f, 1):
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)  # 解析失败即抛错
        assert ev["schema_version"] == "agent-event/1.0", f"line {i}: schema_version"
        assert ev["actor"]["pid"] > 0, f"line {i}: actor.pid"
        assert ev["operation"]["verb"], f"line {i}: verb"
        assert ev["agent"]["id"] == "fake", f"line {i}: agent.id"
        events.append(ev)

verbs = {e["operation"]["verb"] for e in events}
# 注意：NOTIFY_UIPC_CONNECT 在 macOS 26.5.2 实测不投递（SDK 注释显示该事件
# 带 AUTH 缓存键，生产实践均订阅 AUTH 变体），NOTIFY-only 约束下 ipc 感知为
# UIPC_BIND（server 侧），详见 README 已知限制。
need = {"exec", "spawn", "create", "open", "write", "move", "delete",
        "truncate", "ipc_bind", "net_connect", "net_close", "exit"}
missing = need - verbs
assert not missing, f"缺少事件 verb: {missing}（实际: {sorted(verbs)}）"

# actor_process 归因：agent 进程操作目录外文件
# （内核上报 realpath：/tmp → /private/tmp，故用后缀匹配）
outside = [e for e in events if e["operation"]["verb"] == "create"
           and e["target"].get("path", "").endswith("/es-sensor-e2e-outside.txt")]
assert outside and outside[0]["agent"]["match"] in ("actor_process", "both"), \
    "agent 目录外文件操作应归因为 actor_process"
assert outside[0]["actor"]["origin"]["agent_root_executable"].endswith("/fake-agent/bin/agent"), \
    "origin 应回溯到 agent 根"

# target_path 归因：外部进程触碰目录内文件
ext = [e for e in events if e["operation"]["verb"] == "create"
       and e["target"].get("path", "").endswith("external-touch.txt")]
assert ext and ext[0]["agent"]["match"] == "target_path", \
    "外部进程触碰目录内文件应归因为 target_path"

# write 事件来自 CLOSE(modified=true)
writes = [e for e in events if e["operation"]["verb"] == "write"]
assert all(e["operation"]["es_event"]["type"] == "ES_EVENT_TYPE_NOTIFY_CLOSE"
           for e in writes), "write 应全部由 NOTIFY_CLOSE 归一化"

# net 事件为快照语义，含 :443 的 connect 与 close
for v in ("net_connect", "net_close"):
    nets = [e for e in events if e["operation"]["verb"] == v]
    assert nets and all(e["operation"]["outcome"] == "snapshot" for e in nets)
    assert any(e["target"].get("remote", "").endswith(":443") for e in nets), \
        f"{v} 应捕获到 :443 端点"

# exec 事件带 argv
execs = [e for e in events if e["operation"]["verb"] == "exec"]
assert any(e["target"].get("program", {}).get("argv") for e in execs), \
    "exec 事件应带 argv"

print(f"PASS: {len(events)} 事件, verbs={sorted(verbs)}")
PYEOF

echo "== e2e PASS"
