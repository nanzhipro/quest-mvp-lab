#!/bin/bash
# es-agent-sensor 构建签名：cargo release → 组 .app bundle → codesign → 校验。
#
# ES client 必须：.app 壳 + embedded.provisionprofile + endpoint-security.client
# entitlement（裸 Mach-O 无法内嵌 profile）。
#
# 使用前准备：
#   1. 把含 endpoint-security 权限的 provisioning profile 放到
#      packaging/es-agent-sensor.provisionprofile（或用 PROFILE 指定路径）。
#      profile 属敏感资产，绝不提交进库（.gitignore 已排除）。
#   2. 可变参数均可用环境变量覆盖，例如：
#      BUNDLE_ID=com.example.sensor IDENTITY="Developer ID Application: Your Name (TEAMID)" ./scripts/build.sh
#
# 合规：签名产物（*.app）仅用于本机测试，不进库；测试结束 scripts/e2e.sh 会清理。
set -euo pipefail
cd "$(dirname "$0")/.."   # 项目根

APP="ESAgentSensor.app"
BUNDLE_ID="${BUNDLE_ID:-com.nanzhipro.esagentsensor}"
PROFILE="${PROFILE:-./packaging/es-agent-sensor.provisionprofile}"
# 默认取本机钥匙串中第一个 Developer ID Application 证书
IDENTITY="${IDENTITY:-$(security find-identity -v | sed -n 's/.*"\(Developer ID Application: [^"]*\)"/\1/p' | head -1)}"

if [ -z "$IDENTITY" ]; then
  echo "未找到 Developer ID Application 证书，请用 IDENTITY 指定" >&2
  exit 1
fi
if [ ! -f "$PROFILE" ]; then
  echo "缺少 provisioning profile：$PROFILE" >&2
  echo "请在 Apple 开发者后台为 $BUNDLE_ID 创建含 Endpoint Security 权限的 profile，"
  echo "下载到 packaging/ 目录（或用 PROFILE 指定路径）" >&2
  exit 1
fi

cargo build --release --quiet

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp target/release/es-agent-sensor "$APP/Contents/MacOS/es-agent-sensor"
sed "s/__BUNDLE_ID__/$BUNDLE_ID/" packaging/Info.plist > "$APP/Contents/Info.plist"
cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"

# --timestamp=none：本地验证不依赖时间戳服务器；对外分发公证时去掉
codesign --force --options runtime --timestamp=none \
  --entitlements packaging/es-agent-sensor.entitlements \
  --sign "$IDENTITY" "$APP"

codesign --verify --strict --verbose=1 "$APP"
echo "--- entitlements ---"
codesign -d --entitlements :- "$APP"
echo "--- OK: $APP  (bundle id: $BUNDLE_ID, identity: $IDENTITY) ---"
echo "run: sudo ./$APP/Contents/MacOS/es-agent-sensor --agent hermes=\$HOME/.hermes"
