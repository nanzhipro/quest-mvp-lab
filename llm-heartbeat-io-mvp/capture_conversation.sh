#!/usr/bin/env bash
# Capture a multi-turn agent conversation (tool calls + skill loads) through the proxy.
#
#   ./capture_conversation.sh <workdir> "<prompt 1>" ["<prompt 2>" ...]
#
# All prompts run in ONE Hermes session: the first is sent as the opening turn, the
# rest are sent with `--resume <session_id>` so the model sees its own tool calls and
# the tool results between turns. Every HTTP exchange is recorded by capture_proxy.py.
set -euo pipefail

WORKDIR="${1:?usage: capture_conversation.sh <workdir> \"prompt\" [\"prompt 2\" ...]}"
shift
[ "$#" -ge 1 ] || { echo "need at least one prompt" >&2; exit 1; }

PORT="${HB_PORT:-8899}"
TARGET="${HB_TARGET:-https://api.deepseek.com}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REAL_HOME="$(cd "${HB_REAL_HERMES_HOME:-$HOME/.hermes}" && pwd)"
TMP_HOME="/tmp/hb-hermes-home"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN="$HERE/runs/${STAMP}_conversation"
mkdir -p "$RUN/raw"
echo "== run dir: $RUN"
echo "== workdir: $WORKDIR"

rm -rf "$TMP_HOME"; mkdir -p "$TMP_HOME"
for entry in "$REAL_HOME"/*; do
  [ "$(basename "$entry")" = ".env" ] && continue
  ln -s "$entry" "$TMP_HOME/$(basename "$entry")"
done
python3 - "$REAL_HOME/.env" "$TMP_HOME/.env" "$PORT" <<'PY'
import sys, re
src, dst, port = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"http://127.0.0.1:{port}"
out = [f"DEEPSEEK_BASE_URL={url}\n" if re.match(r"\s*DEEPSEEK_BASE_URL\s*=", l) else l
       for l in open(src, encoding="utf-8-sig", errors="replace")]
open(dst, "w", encoding="utf-8").write("".join(out))
print(f"[home] .env -> DEEPSEEK_BASE_URL={url}")
PY

python3 "$HERE/capture_proxy.py" --port "$PORT" --target "$TARGET" --run-dir "$RUN" \
  > "$RUN/proxy.log" 2>&1 &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PORT" && break; sleep 0.3; done

SID=""
n=0
for PROMPT in "$@"; do
  n=$((n+1))
  echo
  echo "== turn $n: $PROMPT"
  if [ -z "$SID" ]; then
    OUT="$(cd "$WORKDIR" && TERMINAL_CWD="$WORKDIR" HERMES_HOME="$TMP_HOME" \
            hermes chat -q "$PROMPT" --oneshot 2>&1 | tail -40)"
  else
    OUT="$(cd "$WORKDIR" && TERMINAL_CWD="$WORKDIR" HERMES_HOME="$TMP_HOME" \
            hermes chat --resume "$SID" -q "$PROMPT" --oneshot 2>&1 | tail -40)"
  fi
  echo "$OUT" | sed 's/^/   /'
  SID="$(printf '%s\n' "$OUT" | grep -oE 'resume [0-9]{8}_[0-9]{6}_[0-9a-f]{6}' | head -1 | awk '{print $2}')"
  [ -n "$SID" ] && echo "   [session $SID]"
done

kill $PROXY_PID 2>/dev/null || true
trap - EXIT
sleep 0.5
echo
python3 "$HERE/show_conversation.py" "$RUN"
