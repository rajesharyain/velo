#!/usr/bin/env bash
# Velo — one-click workflow trigger.
# Double-click this file in Finder to run the workflow immediately.

LOG="$HOME/Library/Logs/Velo/velo-runner.log"
RUNNER="$HOME/Library/Scripts/velo-runner.sh"

clear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Velo — Manual Trigger"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ ! -f "$RUNNER" ]]; then
  echo "  ERROR: Runner not found at $RUNNER"
  echo "  Run: bash scripts/install_schedule.sh first."
  echo ""
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "  Starting workflow... (streaming log below)"
echo "  Full log: $LOG"
echo ""

# Run the runner in background, stream its log output in real time
bash "$RUNNER" &
RUNNER_PID=$!
sleep 1

# Tail the log until the runner finishes
tail -n 0 -f "$LOG" &
TAIL_PID=$!

wait "$RUNNER_PID"
EXIT_CODE=$?
sleep 1
kill "$TAIL_PID" 2>/dev/null

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "  ✅  Workflow triggered successfully!"
else
  echo "  ❌  Runner exited with code $EXIT_CODE — check log above."
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -r -p "Press Enter to close..."
