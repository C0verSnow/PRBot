#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${CI:-}" != "true" || "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "只允许在远端 macOS arm64 CI 中构建，禁止本地编译验证。" >&2
  exit 1
fi
python -m PyInstaller --noconfirm --clean packaging/PRBot.spec
APP="dist/PRBot.app"
# This development build uses an ad hoc signature, NOT a Developer ID signature.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
mkdir -p dist/dmg-content
# ditto preserves the bundle symlinks and embedded runtime.
ditto "$APP" dist/dmg-content/PRBot.app
ln -s /Applications dist/dmg-content/Applications
cp 使用手册.md dist/dmg-content/
cat > dist/dmg-content/测试版说明.txt <<'NOTICE'
PRBot v0.1.0 开发测试版：Apple Silicon / macOS 14+
仅 ad hoc 签名，没有 Developer ID 签名、没有 Apple 公证。
请阅读使用手册；此文件不能代表已通过 Gatekeeper 和真实 Telegram 安装验收。
NOTICE
hdiutil create -volname PRBot -srcfolder dist/dmg-content -ov -format UDZO \
  dist/PRBot-v0.1.0-macos-arm64.dmg
shasum -a 256 dist/PRBot-v0.1.0-macos-arm64.dmg > dist/SHA256SUMS
