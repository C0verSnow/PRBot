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
[[ -f "$MOUNT/.env.example" ]]
[[ -f "$TEST_HOME/PRBot.app/Contents/Resources/.env.example" ]]
# Run with an empty HOME and without source PYTHONPATH or an external Python PATH.
# An invalid Token is intentionally passed so this check never accesses a real credential.
VERSION="$(env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin "$BIN" version)"
[[ "$VERSION" == "0.1.0" ]]
env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin TELEGRAM_BOT_TOKEN=bad "$BIN" run
STATUS="$TEST_HOME/Library/Application Support/PRBot/status.json"
/usr/bin/grep -q 'halted' "$STATUS"
/usr/bin/grep -q 'Token' "$STATUS"
# Validate the installed binary reads .env from the selected workspace without shell env.
mkdir -p "$TEST_HOME/PRBot-workspaces"
python - "$TEST_HOME" <<'PYCONFIG'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
(root / "Library/Application Support/PRBot/config.json").write_text(
    json.dumps({"PRBOT_WORK_ROOT": str(root)})
)
PYCONFIG
chmod 600 "$TEST_HOME/Library/Application Support/PRBot/config.json"
# A format-valid dummy token plus an invalid log level fails before any API request.
# Checking the distinct log-level error proves .env was actually read.
cat > "$TEST_HOME/PRBot-workspaces/.env" <<'ENV'
TELEGRAM_BOT_TOKEN=123456:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
PRBOT_LOG_LEVEL=TRACE
ENV
chmod 600 "$TEST_HOME/PRBot-workspaces/.env"
env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin "$BIN" run
/usr/bin/grep -q 'halted' "$STATUS"
/usr/bin/grep -q 'PRBOT_LOG_LEVEL' "$STATUS"
env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin "$BIN" status > "$TEST_HOME/env-status.json"
/usr/bin/grep -q 'PRBot-workspaces/.env' "$TEST_HOME/env-status.json"
env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin "$BIN" sdk-check > "$TEST_HOME/sdk-check.json"
/usr/bin/grep -q '"initialized": true' "$TEST_HOME/sdk-check.json"
# Real bundled WebKit load + native message bridge + navigation/settings + rendered screenshot.
# This uses a dummy config and does not start services or call Telegram.
env -i HOME="$TEST_HOME" PATH=/usr/bin:/bin CI=true \
  PRBOT_UI_CHECK_OUTPUT="$PWD/dist/PRBot-ui.png" "$BIN" ui-check
codesign --verify --deep --strict "$TEST_HOME/PRBot.app"
echo 'DMG mount/copy + embedded runtime + workspace env configuration halt: passed'
