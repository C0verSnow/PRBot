#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${CI:-}" != "true" || "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "只允许在远端 macOS arm64 CI 中构建，禁止本地编译验证。" >&2
  exit 1
fi
for required_file in README.md .env.example; do
  if [[ ! -f "$required_file" ]]; then
    echo "缺少打包必需文件：$required_file；请恢复文件后重新运行远端 CI。" >&2
    exit 1
  fi
done
python -m PyInstaller --noconfirm --clean packaging/PRBot.spec
APP="dist/PRBot.app"
# This development build uses an ad hoc signature, NOT a Developer ID signature.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
mkdir -p dist/dmg-content
# ditto preserves the bundle symlinks and embedded runtime.
ditto "$APP" dist/dmg-content/PRBot.app
ln -s /Applications dist/dmg-content/Applications
cp README.md .env.example dist/dmg-content/
cat > dist/dmg-content/DEVELOPMENT-NOTICE.txt <<'NOTICE'
PRBot v0.1.0 development build: Apple Silicon / macOS 14+
Ad hoc signature only; no Developer ID signature or Apple notarization.
Read README.md. This notice does not establish Gatekeeper or live-service acceptance.
NOTICE
hdiutil create -volname PRBot -srcfolder dist/dmg-content -ov -format UDZO \
  dist/PRBot-v0.1.0-macos-arm64.dmg
shasum -a 256 dist/PRBot-v0.1.0-macos-arm64.dmg > dist/SHA256SUMS
