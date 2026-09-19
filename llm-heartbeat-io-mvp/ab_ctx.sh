#!/usr/bin/env bash
# Controlled A/B: does the working directory change the heartbeat's input?
#
#   ./ab_ctx.sh [PORT]                 # A 组宿主 = $PWD（默认）
#   HB_WORKSPACE_DIR=/path/to/ws ./ab_ctx.sh    # 显式指定 A 组宿主目录（通常是工作区根）
#   PORT    local proxy port                (default 8899)
#
# Runs the identical query ('ping') twice through the capture proxy — once with cwd
# inside the workspace root (AGENTS.md/CLAUDE.md discoverable), once with cwd=/tmp
# (no project context files). Same profile (mirrored HERMES_HOME), same model.
# Prints the provider-reported prompt token counts and the request byte sizes so the
# delta can be attributed to project context, then dumps run dir for a prompt diff.
set -euo pipefail

PORT="${1:-8899}"
PROMPT_HOST="${HB_WORKSPACE_DIR:-$PWD}"   # A 组的宿主目录：指向工作区根，project context 才可达
HERE="$(cd "$(dirname "$0")" && pwd)"
REAL_HOME="$(cd "${HB_REAL_HERMES_HOME:-$HOME/.hermes}" && pwd)"
TMP_HOME="/tmp/hb-hermes-home"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN="$HERE/runs/${STAMP}_ab_cwd"
mkdir -p "$RUN/raw"
echo "== A/B run dir: $RUN"

rm -rf "$TMP_HOME"; mkdir -p "$TMP_HOME"
for entry in "$REAL_HOME"/*; do
  [ "$(basename "$entry")" = ".env" ] && continue
  ln -s "$entry" "$TMP_HOME/$(basename "$entry")"
done
python3 - "$REAL_HOME/.env" "$TMP_HOME/.env" "$PORT" <<'PY'
import sys, re
src, dst, port = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"http://127.0.0.1:{port}"
out = []
for line in open(src, encoding="utf-8-sig", errors="replace"):
    out.append(f"DEEPSEEK_BASE_URL={url}\n" if re.match(r"\s*DEEPSEEK_BASE_URL\s*=", line) else line)
open(dst, "w", encoding="utf-8").write("".join(out))
print(f"[home] .env -> DEEPSEEK_BASE_URL={url}")
PY

python3 "$HERE/capture_proxy.py" --port "$PORT" --target https://api.deepseek.com --run-dir "$RUN" \
  > "$RUN/proxy.log" 2>&1 &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PORT" && break; sleep 0.3; done

echo "== A: cwd = $PROMPT_HOST (project context discoverable) ..."
( cd "$PROMPT_HOST" && TERMINAL_CWD="$PROMPT_HOST" HERMES_HOME="$TMP_HOME" hermes chat -q 'ping' --oneshot >/dev/null 2>&1 ) || true
echo "== B: cwd=/tmp and TERMINAL_CWD/HERMES_CWD unset (workspace context unreachable) ..."
( cd /tmp && env -u TERMINAL_CWD -u HERMES_CWD HERMES_HOME="$TMP_HOME" hermes chat -q 'ping' --oneshot >/dev/null 2>&1 ) || true

kill $PROXY_PID 2>/dev/null || true
trap - EXIT
sleep 0.5

python3 - "$RUN" <<'PY'
import json, os, sys
run = sys.argv[1]
events = [json.loads(l) for l in open(os.path.join(run, "events.jsonl"), encoding="utf-8") if l.strip()]
reqs = [r for r in events if r["direction"] == "request" and (r.get("body_json") or {}).get("tools")]
resps = {r["seq"]: r for r in events if r["direction"] == "response"}
print(f"\n{'label':<34}{'sysprompt_chars':>16}{'tools':>7}{'req_bytes':>12}{'prompt_tokens':>15}")
prompts = {}
for i, r in enumerate(reqs):
    b = r["body_json"]
    sysp = next(m["content"] for m in b["messages"] if m["role"] == "system")
    # usage rides in the trailing SSE event
    raw = [os.path.join(run, "raw", f"resp_{r['seq']:04d}.{e}") for e in ("json", "bin")]
    usage = None
    for p in raw:
        if os.path.exists(p):
            for line in open(p, encoding="utf-8", errors="replace"):
                if line.startswith("data:") and '"usage"' in line:
                    try:
                        u = json.loads(line[5:].strip()).get("usage")
                        if u: usage = u
                    except Exception:
                        pass
    label = f"#{r['seq']} {'A cwd=workspace' if len(sysp) > 40000 else 'B cwd=/tmp'}"
    print(f"{label:<34}{len(sysp):>16,}{len(b['tools']):>7}{r['body_bytes']:>12,}"
          f"{(usage or {}).get('prompt_tokens', 0):>15,}")
    prompts[r["seq"]] = sysp
seqs = sorted(prompts)
if len(seqs) == 2:
    a, b = prompts[seqs[0]], prompts[seqs[1]]
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    common = 0
    for x, y in zip(lo, hi):
        if x != y: break
        common += 1
    print(f"\ncommon prefix = {common:,} chars; longer prompt = {len(hi):,} chars; "
          f"delta = {len(hi)-len(lo):,} chars")
    open(os.path.join(run, "prompt_A.txt"), "w", encoding="utf-8").write(a)
    open(os.path.join(run, "prompt_B.txt"), "w", encoding="utf-8").write(b)
    print("prompts written to prompt_A.txt / prompt_B.txt for diffing")
PY
