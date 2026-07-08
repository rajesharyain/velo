#!/usr/bin/env bash
# Removes the Velo LaunchAgent — stops the daily schedule.
set -euo pipefail

PLIST_LABEL="com.velo.runner"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

if [[ -f "$PLIST_FILE" ]]; then
  launchctl unload "$PLIST_FILE" 2>/dev/null || true
  rm "$PLIST_FILE"
  echo "✓ Velo schedule removed. The plist has been deleted."
else
  echo "Nothing to remove — $PLIST_FILE does not exist."
fi
