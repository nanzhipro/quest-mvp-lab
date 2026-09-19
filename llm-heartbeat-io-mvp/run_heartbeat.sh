#!/usr/bin/env bash
# Drive one heartbeat capture cycle: proxy up -> 3 client calls -> proxy down -> analyse.
#
#   ./run_heartbeat.sh [PORT] [TARGET]
#   PORT    local proxy port                (default 8899)
#   TARGET  real provider base URL          (default https://api.deepseek.com)
#
# Produces runs/<UTC-local timestamped dir>/ containing events.jsonl, raw/ (verbatim
# request/response bodies), summary.json and the analyse.py text report.
#
# Why a mirrored HERMES_HOME: Hermes loads ~/.hermes/.env with override=True, so a
# DEEPSEEK_BASE_URL exported in the shell is ignored. The temp home symlinks every
# profile entry (skills, memories, config.yaml, state.db, mcp-tokens ...) and copies
# only .env, repointing the base URL at the proxy. The live profile is never modified.
set -euo pipefail

PORT="${1:-8899}"
TARGET="${2:-https://api.deepseek.com}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REAL_HOME="${HB_REAL_HERMES_HOME:-$HOME/.hermes}"   # NOT $HERMES_REAL_HOME: Hermes sets that to $HOME
TMP_HOME="/tmp/hb-hermes-home"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN="$HERE/runs/$STAMP"

echo "== run dir: $RUN"
mkdir -p "$RUN/raw"

# ---------------------------------------------------------------- temp home
rm -rf "$TMP_HOME"; mkdir -p "$TMP_HOME"
for entry in "$REAL_HOME"/*; do
  name="$(basename "$entry")"
  [ "$name" = ".env" ] && continue
  ln -s "$entry" "$TMP_HOME/$name"
done
python3 - "$REAL_HOME/.env" "$TMP_HOME/.env" "$PORT" <<'PY'
import sys, re
src, dst, port = sys.argv[1], sys.argv[2], sys.argv[3]
new_url = f"http://127.0.0.1:{port}"
out, hit = [], 0
for line in open(src, encoding="utf-8-sig", errors="replace"):
    if re.match(r"\s*DEEPSEEK_BASE_URL\s*=", line):
        out.append(f"DEEPSEEK_BASE_URL={new_url}\n"); hit += 1
    else:
        out.append(line)
if not hit:
    out.append(f"DEEPSEEK_BASE_URL={new_url}\n")
open(dst, "w", encoding="utf-8").write("".join(out))
print(f"[home] .env copied, DEEPSEEK_BASE_URL -> {new_url} ({hit} line(s) rewritten)")
PY

# ------------------------------------------------------------------- proxy
python3 "$HERE/capture_proxy.py" --port "$PORT" --target "$TARGET" --run-dir "$RUN" \
  > "$RUN/proxy.log" 2>&1 &
PROXY_PID=$!
cleanup() { kill "$PROXY_PID" 2>/dev/null || true; }
trap cleanup EXIT

for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PORT" && break; sleep 0.3; done
nc -z 127.0.0.1 "$PORT" || { echo "!! proxy failed to bind"; exit 1; }
echo "== proxy up (pid $PROXY_PID) -> $TARGET"

# --------------------------------------------------- H1: full agent heartbeat
echo "== H1 hermes chat -q 'ping' (full agent payload) ..."
HERMES_HOME="$TMP_HOME" hermes chat -q 'ping' --oneshot 2>&1 | tail -12 || true

# ---------------------------------------------- M1/M2: bare-model heartbeats
MODEL="$(python3 -c '
import json,os
p=os.path.join(os.environ["RUN"],"events.jsonl")
for l in open(p):
    r=json.loads(l)
    if r.get("direction")=="request" and r.get("body_json",{}).get("model"):
        print(r["body_json"]["model"]); break
else: print("deepseek-v4-flash")' RUN="$RUN" 2>/dev/null || echo deepseek-v4-flash)"
echo "== wire model seen in H1: $MODEL"

API_KEY="$(python3 -c 'import re,os;print([l.split("=",1)[1].strip() for l in open(os.path.expanduser("~/.hermes/.env"),encoding="utf-8-sig") if l.startswith("DEEPSEEK_API_KEY=")][0])')"

echo "== M1 bare heartbeat, non-stream ..."
curl -sS --max-time 180 "http://127.0.0.1:$PORT/chat/completions" \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"stream\":false}" \
  -o "$RUN/raw/m1_client_view.json"
echo "== M2 bare heartbeat, stream=true ..."
curl -sS --max-time 180 -N "http://127.0.0.1:$PORT/chat/completions" \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"stream\":true}" \
  -o "$RUN/raw/m2_client_view.sse"

cleanup
trap - EXIT
sleep 0.5

echo
python3 "$HERE/analyze.py" "$RUN"
echo "== raw files =="
ls -la "$RUN/raw"
