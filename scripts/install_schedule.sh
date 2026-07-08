#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install_schedule.sh — registers the Velo daily runner as a macOS LaunchAgent.
#
# Usage:
#   ./scripts/install_schedule.sh              # runs at 09:00 and 18:00 (default)
#   ./scripts/install_schedule.sh 8 20         # runs at 08:00 and 20:00
#
# What it does:
#   1. Creates velo.config.env from example (if it doesn't exist yet)
#   2. Writes ~/Library/LaunchAgents/com.velo.runner.plist
#   3. Loads the plist so it takes effect immediately (no reboot needed)
#   4. Activates the n8n workflow so the webhook URL is live
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VELO_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_LABEL="com.velo.runner"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
RUNNER_SCRIPT="$SCRIPT_DIR/run_velo.sh"
CONFIG_FILE="$VELO_DIR/velo.config.env"
CONFIG_EXAMPLE="$VELO_DIR/velo.config.env.example"
LOG_DIR="$VELO_DIR/logs"

HOUR1="${1:-9}"
HOUR2="${2:-18}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Velo Scheduler — Install"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 1: Create config if missing ─────────────────────────────────────────
if [[ ! -f "$CONFIG_FILE" ]]; then
  cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
  # Auto-fill VELO_DIR
  sed -i '' "s|^VELO_DIR=.*|VELO_DIR=$VELO_DIR|" "$CONFIG_FILE"
  echo "  ✓ Created: $CONFIG_FILE"
  echo ""
  echo "  ⚠️  ACTION REQUIRED:"
  echo "  Open $CONFIG_FILE and set EXCEL_PATH to your Excel file."
  echo "  Then re-run this script."
  echo ""
  exit 0
else
  # Ensure VELO_DIR is correct
  sed -i '' "s|^VELO_DIR=.*|VELO_DIR=$VELO_DIR|" "$CONFIG_FILE"
  echo "  ✓ Config: $CONFIG_FILE"
fi

# ── Step 2: Validate Excel path ───────────────────────────────────────────────
set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

if [[ -z "${EXCEL_PATH:-}" ]]; then
  echo ""
  echo "  ERROR: EXCEL_PATH is not set in $CONFIG_FILE"
  echo "  Edit the file and set EXCEL_PATH=/path/to/your/reels-queue.xlsx"
  echo ""
  exit 1
fi

if [[ ! -f "$EXCEL_PATH" ]]; then
  echo ""
  echo "  WARNING: Excel file not found at: $EXCEL_PATH"
  echo "  The schedule will still be installed but runs will fail until the file exists."
  echo ""
fi

# ── Step 3: Make scripts executable ──────────────────────────────────────────
chmod +x "$RUNNER_SCRIPT"
chmod +x "$SCRIPT_DIR/uninstall_schedule.sh" 2>/dev/null || true
mkdir -p "$LOG_DIR"
echo "  ✓ Scripts are executable"

# ── Step 4: Write LaunchAgent plist ──────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_FILE" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${RUNNER_SCRIPT}</string>
    </array>

    <!-- Run at HOUR1:00 and HOUR2:00 every day -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>
            <integer>${HOUR1}</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
        <dict>
            <key>Hour</key>
            <integer>${HOUR2}</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <!-- Log output -->
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/velo-runner.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/velo-runner-error.log</string>

    <!-- Do not run immediately on load, only on schedule -->
    <key>RunAtLoad</key>
    <false/>

    <!-- PATH so docker/curl are found in the minimal LaunchAgent environment -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
</dict>
</plist>
PLIST_EOF

echo "  ✓ LaunchAgent plist written: $PLIST_FILE"

# ── Step 5: Load the LaunchAgent ─────────────────────────────────────────────
launchctl unload "$PLIST_FILE" 2>/dev/null || true
launchctl load "$PLIST_FILE"
echo "  ✓ LaunchAgent loaded"

# ── Step 6: Activate n8n workflow (so webhook URL is live) ────────────────────
N8N_API_KEY=$(grep "^N8N_API_KEY=" "$VELO_DIR/.env" 2>/dev/null | cut -d'=' -f2- || echo "")
WORKFLOW_ID="UgNKfVqQNN0Rya6m"

if [[ -n "$N8N_API_KEY" ]]; then
  echo "  Activating n8n workflow..."
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PATCH "http://localhost:5678/api/v1/workflows/$WORKFLOW_ID" \
    -H "X-N8N-API-KEY: $N8N_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"active": true}' 2>/dev/null) || HTTP_STATUS="error"

  if [[ "$HTTP_STATUS" == "200" ]]; then
    echo "  ✓ n8n workflow activated (webhook is live)"
  else
    echo "  ⚠️  Could not activate workflow (HTTP $HTTP_STATUS)."
    echo "     Make sure n8n is running, then activate the workflow manually in the n8n UI."
  fi
else
  echo "  ⚠️  N8N_API_KEY not found in .env — activate the workflow manually in n8n UI."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  Schedule installed successfully!"
echo ""
echo "  Runs at: ${HOUR1}:00 and ${HOUR2}:00 every day"
echo "  Excel  : $EXCEL_PATH"
echo "  Log    : $LOG_DIR/velo-runner.log"
echo ""
echo "  To test it right now:"
echo "    bash $RUNNER_SCRIPT"
echo ""
echo "  To change the schedule (e.g. 8am and 9pm):"
echo "    ./scripts/install_schedule.sh 8 21"
echo ""
echo "  To stop the schedule:"
echo "    ./scripts/uninstall_schedule.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
