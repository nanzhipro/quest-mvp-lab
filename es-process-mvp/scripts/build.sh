#!/bin/bash
# es-process-mvp 构建签名：swift release → 组 bundle → codesign → 校验。
#
# 前置（见 README.md「开始前你需要准备」）：
#   1. Endpoint Security 托管权限的 provisioning profile（amfid 启动时校验 embedded profile）
#   2. 本机钥匙串的 Developer ID Application 证书（与 profile 同一 Team）
#
# 签名物料均为占位符，用环境变量替换为你自己的（见 README.md「签名物料替换说明」）：
#   BUNDLE_ID=com.example.esmvp \
#   IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   PROFILE=/path/to/your.provisionprofile \
#   ./scripts/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."   # 项目根

APP="ESProcessMvp.app"
BIN="es-process-mvp"
BUNDLE_ID="${BUNDLE_ID:-com.example.esmvp}"
PROFILE="${PROFILE:-./packaging/esmvp.provisionprofile}"
# 默认取本机钥匙串中第一个 Developer ID Application 证书
IDENTITY="${IDENTITY:-$(security find-identity -v | sed -n 's/.*"\(Developer ID Application: [^"]*\)"/\1/p' | head -1)}"

if [ -z "$IDENTITY" ]; then
  echo "未找到 Developer ID Application 证书，请用 IDENTITY 指定（见 README.md）" >&2
  exit 1
fi
if [ ! -f "$PROFILE" ]; then
  echo "缺少 provisioning profile（PROFILE=$PROFILE）" >&2
  echo "请按 README.md「开始前你需要准备」创建并下载，或放入 packaging/ 目录" >&2
  exit 1
fi

echo "=== swift build -c release ==="
swift build -c release --quiet

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp ".build/release/$BIN" "$APP/Contents/MacOS/$BIN"
sed "s/__BUNDLE_ID__/$BUNDLE_ID/" packaging/Info.plist > "$APP/Contents/Info.plist"
cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"

# --timestamp=none：本地验证不依赖时间戳服务器；对外分发公证时去掉
codesign --force --options runtime --timestamp=none \
  --entitlements packaging/esmvp.entitlements \
  --sign "$IDENTITY" "$APP"

codesign --verify --strict --verbose=1 "$APP"
echo "--- entitlements ---"
codesign -d --entitlements - "$APP" 2>/dev/null | grep -E "endpoint-security|application-identifier|team-identifier"
echo "--- OK: $APP  (bundle id: $BUNDLE_ID, identity: $IDENTITY, profile: $PROFILE) ---"
echo "run: sudo ./$APP/Contents/MacOS/$BIN <config.yaml>   # 见 config.example.yaml"
