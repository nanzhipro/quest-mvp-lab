#!/usr/bin/env bash
#
# Terminal demo of the tool-call sandbox: one `sandbox-center` (resident) plus one
# `sandbox-cli` process per case, each materialising its own Seatbelt profile and
# running `/usr/bin/sandbox-exec → zsh → <tool>`.
#
# Usage: ./scripts/demo.sh            (build first: make build-core)
#
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CENTER="$PROJECT/core/target/release/sandbox-center"
CLI="$PROJECT/core/target/release/sandbox-cli"
POLICY="$PROJECT/policy/default-policy.json"

if [[ ! -x "$CENTER" || ! -x "$CLI" ]]; then
    echo "sandbox binaries not found — run \`make build-core\` first" >&2
    exit 1
fi

if ! "$CLI" --probe >/dev/null 2>&1; then
    echo "seatbelt is not usable in this shell (nested sandbox?) — see the README" >&2
    "$CLI" --probe || true
    exit 1
fi

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sandbox-center-demo.XXXXXX")"
APP_HOME="$ROOT/app-home"
WORKSPACE="$ROOT/workspace"
SOCKET="$ROOT/center.sock"
# Outside every root the policy grants — the write must be denied (and the file
# must never appear).
ESCAPE="$HOME/.sandbox-center-mvp-demo-escape.txt"
mkdir -p "$WORKSPACE/.sc-trash" "$APP_HOME/cache-demo" "$APP_HOME/trash"

CENTER_PID=""
cleanup() {
    if [[ -n "$CENTER_PID" ]]; then
        kill "$CENTER_PID" 2>/dev/null || true
        wait "$CENTER_PID" 2>/dev/null || true
    fi
    rm -rf "$ROOT"
    rm -f "$ESCAPE"
}
trap cleanup EXIT

bold=$'\033[1m'; dim=$'\033[2m'; green=$'\033[32m'; red=$'\033[31m'; cyan=$'\033[36m'; reset=$'\033[0m'

"$CENTER" --app-home "$APP_HOME" --policy "$POLICY" --workspace "$WORKSPACE" \
    --socket "$SOCKET" --quiet >"$ROOT/center.out" 2>&1 &
CENTER_PID=$!
for _ in $(seq 1 50); do
    [[ -S "$SOCKET" ]] && break
    sleep 0.1
done

DESCRIPTOR="$(python3 -c "import json,sys;d=json.load(open('$APP_HOME/center.json'));print(f\"policy={d['policy_id']} rules={d['rules']} socket={d['socket']}\")")"
printf '%s\n' "${bold}sandbox-center${reset} ${dim}pid=$CENTER_PID $DESCRIPTOR${reset}"
printf '%s\n\n' "${dim}workspace=$WORKSPACE  app_home=$APP_HOME${reset}"

printf 'seed\n' > "$WORKSPACE/precious.txt"
printf 'blob\n' > "$APP_HOME/cache-demo/blob.bin"

PASS=0
FAIL=0

run_case() {
    local title="$1" expected="$2" command="$3"
    local actual
    printf '%s\n' "${bold}▸ ${title}${reset}"
    printf '%s\n' "  ${dim}\$ $command${reset}"
    set +e
    "$CLI" --socket "$SOCKET" --cwd "$WORKSPACE" --quiet-events -- /bin/zsh -c "$command" \
        2>&1 | sed 's/^/  /'
    actual=${PIPESTATUS[0]}
    set -e
    if [[ "$actual" == "$expected" ]]; then
        printf '%s\n\n' "  ${green}✔ exit=$actual (expected $expected)${reset}"
        PASS=$((PASS + 1))
    else
        printf '%s\n\n' "  ${red}✘ exit=$actual (expected $expected)${reset}"
        FAIL=$((FAIL + 1))
    fi
}

run_case "写工作区文件 → 放行" 0 \
    "echo 'hello from inside the sandbox' > hello.txt && cat hello.txt"
run_case "写工作区外的文件 → 拒绝" 1 \
    "echo pwned > $ESCAPE && echo escaped"
run_case "删除工作区文件 → 拒绝（删除保护）" 1 \
    "rm precious.txt"
run_case "删除 .sc-trash 内文件 → 放行" 0 \
    "printf bye > .sc-trash/tmp.txt && rm .sc-trash/tmp.txt && echo trash-ok"
run_case "删除缓存目录 → 拒绝 → 自动授权 → 重试成功" 0 \
    "rm $APP_HOME/cache-demo/blob.bin && echo cache-regenerated"
run_case "访问公网 → 拒绝" 1 \
    "/usr/bin/nc -w 2 1.1.1.1 80 </dev/null"
run_case "python3 / node 在沙箱内运行" 0 \
    "python3 -c 'print(6*7)'; node -e 'console.log(6*7)'"
run_case "读取 /etc/passwd → 放行（默认全盘可读）" 0 \
    "head -1 /etc/passwd > /dev/null && echo read-ok"

printf '%s\n' "${bold}审计链${reset}"
AUDIT_FILE="$(ls "$APP_HOME"/audit/*.jsonl | head -1)"
python3 - "$AUDIT_FILE" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    record = json.loads(line)
    data = record['data']
    detail = data.get('target') or data.get('exit_code') or data.get('policy_id') or ''
    extra = ''
    if record['kind'] == 'session.closed':
        extra = f"violations={data['violations']} grants={data['grants']} retried={data['retried']}"
    if record['kind'] == 'grant.auto':
        extra = f"{data['reason']} → {data['root']}"
    print(f"  {record['seq']:>3}  {record['kind']:<18} {str(detail)[:52]:<52} {extra}")
PY
"$CENTER" --verify-audit "$AUDIT_FILE" | sed 's/^/  /'

printf '\n%s\n' "${bold}结果${reset}  ${green}✓ $PASS${reset} / ${red}✗ $FAIL${reset}"
[[ "$FAIL" -eq 0 ]]
