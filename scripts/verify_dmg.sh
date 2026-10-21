#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${CI:-}" != "true" || "$(uname -s)" != "Darwin" ]]; then
  echo "只允许在远端 macOS CI 验证安装包。" >&2
  exit 1
fi
MOUNT="$(mktemp -d)"
TEST_HOME="$(mktemp -d)"
trap 'hdiutil detach "$MOUNT" >/dev/null 2>&1 || true; rm -rf "$TEST_HOME" "$MOUNT"' EXIT
hdiutil attach dist/PRBot-v0.1.0-macos-arm64.dmg -nobrowse -mountpoint "$MOUNT"
hdiutil verify dist/PRBot-v0.1.0-macos-arm64.dmg
ditto "$MOUNT/PRBot.app" "$TEST_HOME/PRBot.app"
BIN="$TEST_HOME/PRBot.app/Contents/MacOS/PRBot"
# Run with an empty HOME and without source PYTHONPATH or an external Python PATH.
# A blank Token is intentionally passed so this check never accesses a real credential.
VERSION="$(env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin "$BIN" version)"
[[ "$VERSION" == "0.1.0" ]]
env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin TELEGRAM_BOT_TOKEN=bad "$BIN" run
STATUS="$TEST_HOME/Library/Application Support/PRBot/status.json"
/usr/bin/grep -q 'halted' "$STATUS"
/usr/bin/grep -q 'Token' "$STATUS"
codesign --verify --deep --strict "$TEST_HOME/PRBot.app"
echo 'DMG mount/copy + embedded runtime + invalid configuration halt: passed'
