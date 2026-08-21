#!/bin/bash
# esmvp-swift（Swift 版）构建签名：swift release → 组 bundle → codesign → 校验。
#
# 使用前准备（详见仓库根 README.md「开始前你需要准备」）：
#   1. 把你的 provisioning profile 放到 packaging/esmvp.provisionprofile（或用 PROFILE 指定路径）
#   2. 可变参数均可用环境变量覆盖，例如：
#      BUNDLE_ID=com.example.esmvp IDENTITY="Developer ID Application: Your Name (TEAMID)" ./scripts/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."   # 项目根（swift/）

APP="ESMvpSwift.app"
BUNDLE_ID="${BUNDLE_ID:-com.example.esmvp}"
PROFILE="${PROFILE:-./packaging/esmvp.provisionprofile}"
# 默认取本机钥匙串中第一个 Developer ID Application 证书
IDENTITY="${IDENTITY:-$(security find-identity -v | sed -n 's/.*"\(Developer ID Application: [^"]*\)"/\1/p' | head -1)}"

if [ -z "$IDENTITY" ]; then
  echo "未找到 Developer ID Application 证书，请先在 Apple 开发者后台创建（见 README.md），或用 IDENTITY 指定" >&2
  exit 1
fi
if [ ! -f "$PROFILE" ]; then
  echo "缺少 provisioning profile：$PROFILE" >&2
  echo "请按 README.md「开始前你需要准备」创建并下载到 packaging/ 目录" >&2
  exit 1
fi

swift build -c release --quiet

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/esmvp-swift "$APP/Contents/MacOS/esmvp-swift"
sed "s/__BUNDLE_ID__/$BUNDLE_ID/" packaging/Info.plist > "$APP/Contents/Info.plist"
cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"

# --timestamp=none：本地验证不依赖时间戳服务器；对外分发公证时去掉
codesign --force --options runtime --timestamp=none \
  --entitlements packaging/esmvp.entitlements \
  --sign "$IDENTITY" "$APP"

codesign --verify --strict --verbose=1 "$APP"
echo "--- entitlements ---"
codesign -d --entitlements :- "$APP"
echo "--- OK: $APP  (bundle id: $BUNDLE_ID, identity: $IDENTITY) ---"
echo "run: sudo ./$APP/Contents/MacOS/esmvp-swift [--watch <dir>]... [--cache] [--verbose]"
