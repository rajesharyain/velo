#!/usr/bin/env bash
# Removes the Velo LaunchAgent and cleans up ~/Library runtime files.
set -euo pipefail

PLIST_LABEL="com.velo.runner"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
APP_SUPPORT="$HOME/Library/Application Support/Velo"
LIB_RUNNER="$HOME/Library/Scripts/velo-runner.sh"

if [[ -f "$PLIST_FILE" ]]; then
  launchctl unload "$PLIST_FILE" 2>/dev/null || true
  rm "$PLIST_FILE"
  echo "✓ LaunchAgent removed"
else
  echo "  (no plist found — already uninstalled)"
fi

[[ -f "$LIB_RUNNER" ]] && rm "$LIB_RUNNER" && echo "✓ Runner script removed"
[[ -d "$APP_SUPPORT" ]] && rm -rf "$APP_SUPPORT" && echo "✓ ~/Library/Application Support/Velo removed"

echo "✓ Velo schedule fully removed."
echo "  Logs are kept at ~/Library/Logs/Velo — delete manually if needed."
