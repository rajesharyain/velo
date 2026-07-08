#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_velo.sh — triggered by the macOS LaunchAgent twice a day.
#
# What it does:
#   1. Loads velo.config.env (Excel path, trigger count, etc.)
#   2. Updates TRAVEL_PRICES_EXCEL_PATH in .env from the config
#   3. Starts Docker Desktop if it isn't running
#   4. Brings up velo + n8n containers (docker compose up -d)
#   5. Waits for both services to be healthy
#   6. Calls the n8n webhook to fire the workflow N times
#   7. Logs everything to logs/velo-runner.log
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VELO_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$VELO_DIR/velo.config.env"
LOG_DIR="$VELO_DIR/logs"
LOG_FILE="$LOG_DIR/velo-runner.log"
MAX_LOG_LINES=5000   # rotate log when it grows too large

# Ensure logs directory exists
mkdir -p "$LOG_DIR"

# ── Logging ──────────────────────────────────────────────────────────────────
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Rotate log file if it exceeds MAX_LOG_LINES
rotate_log() {
  if [[ -f "$LOG_FILE" ]]; then
    local lines
    lines=$(wc -l < "$LOG_FILE" 2>/dev/null || echo 0)
    if [[ "$lines" -gt "$MAX_LOG_LINES" ]]; then
      mv "$LOG_FILE" "${LOG_FILE}.old"
      log "Log rotated (was ${lines} lines)"
    fi
  fi
}
rotate_log

log "════════════════════════════════════════════"
log "  Velo Daily Run — $(date '+%A %d %b %Y %H:%M')"
log "════════════════════════════════════════════"

# ── Load config ───────────────────────────────────────────────────────────────
if [[ ! -f "$CONFIG_FILE" ]]; then
  log "ERROR: $CONFIG_FILE not found."
  log "       Run: cp $VELO_DIR/velo.config.env.example $CONFIG_FILE"
  log "       Then edit it and set EXCEL_PATH."
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

EXCEL_PATH="${EXCEL_PATH:-}"
TRIGGER_COUNT="${TRIGGER_COUNT:-1}"
TRIGGER_GAP_SECONDS="${TRIGGER_GAP_SECONDS:-15}"
VELO_DIR="${VELO_DIR:-$VELO_DIR}"

log "Config loaded from: $CONFIG_FILE"
log "Excel path  : ${EXCEL_PATH:-<not set>}"
log "Trigger count: $TRIGGER_COUNT"

# ── Validate Excel path ───────────────────────────────────────────────────────
if [[ -z "$EXCEL_PATH" ]]; then
  log "ERROR: EXCEL_PATH is not set in $CONFIG_FILE. Aborting."
  exit 1
fi

if [[ ! -f "$EXCEL_PATH" ]]; then
  log "ERROR: Excel file not found at: $EXCEL_PATH"
  log "       Check EXCEL_PATH in $CONFIG_FILE"
  exit 1
fi

log "Excel file found ✓"

# ── Write Excel path into .env so the velo container picks it up ──────────────
ENV_FILE="$VELO_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  if grep -q "^TRAVEL_PRICES_EXCEL_PATH=" "$ENV_FILE"; then
    sed -i '' "s|^TRAVEL_PRICES_EXCEL_PATH=.*|TRAVEL_PRICES_EXCEL_PATH=$EXCEL_PATH|" "$ENV_FILE"
  else
    # Ensure file ends with a newline before appending
    [[ -n "$(tail -c1 "$ENV_FILE")" ]] && echo "" >> "$ENV_FILE"
    echo "TRAVEL_PRICES_EXCEL_PATH=$EXCEL_PATH" >> "$ENV_FILE"
  fi
  log ".env updated: TRAVEL_PRICES_EXCEL_PATH=$EXCEL_PATH"
fi

# ── Ensure Docker is running ─────────────────────────────────────────────────
# Add Homebrew and standard Docker paths
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

if ! docker info >/dev/null 2>&1; then
  log "Docker is not running — starting Docker Desktop..."
  open -a Docker
  # Wait up to 90 seconds for Docker to be ready
  for i in $(seq 1 18); do
    sleep 5
    if docker info >/dev/null 2>&1; then
      log "Docker Desktop started ✓"
      break
    fi
    log "Waiting for Docker... ($((i * 5))s)"
  done
  if ! docker info >/dev/null 2>&1; then
    log "ERROR: Docker Desktop did not start within 90 seconds. Aborting."
    exit 1
  fi
else
  log "Docker is running ✓"
fi

# ── Start containers ──────────────────────────────────────────────────────────
log "Starting velo + n8n containers..."
cd "$VELO_DIR"
docker compose up -d 2>&1 | while IFS= read -r line; do log "  docker: $line"; done
log "Containers started ✓"

# ── Wait for velo API ─────────────────────────────────────────────────────────
log "Waiting for velo API (http://localhost:8000/api/health)..."
velo_ready=0
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
    velo_ready=1
    break
  fi
  sleep 3
done

if [[ "$velo_ready" -eq 0 ]]; then
  log "ERROR: Velo API did not become healthy after 90s. Aborting."
  exit 1
fi
log "Velo API healthy ✓"

# ── Wait for n8n ──────────────────────────────────────────────────────────────
log "Waiting for n8n (http://localhost:5678/healthz)..."
n8n_ready=0
for i in $(seq 1 20); do
  if curl -sf http://localhost:5678/healthz >/dev/null 2>&1; then
    n8n_ready=1
    break
  fi
  sleep 3
done

if [[ "$n8n_ready" -eq 0 ]]; then
  log "ERROR: n8n did not become healthy after 60s. Aborting."
  exit 1
fi
log "n8n healthy ✓"

# ── Fire the workflow ─────────────────────────────────────────────────────────
WEBHOOK_URL="http://localhost:5678/webhook/velo-daily-run"

log "Triggering workflow $TRIGGER_COUNT time(s) via: $WEBHOOK_URL"

for i in $(seq 1 "$TRIGGER_COUNT"); do
  log "  → Trigger $i/$TRIGGER_COUNT..."
  HTTP_STATUS=$(curl -s -o /tmp/velo_trigger_resp.txt -w "%{http_code}" \
    -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "{\"source\":\"launchagent\",\"run\":$i}" 2>&1) || HTTP_STATUS="error"

  RESP_BODY=$(cat /tmp/velo_trigger_resp.txt 2>/dev/null || echo "")
  log "     Status: $HTTP_STATUS"
  if [[ -n "$RESP_BODY" ]]; then
    log "     Response: $(echo "$RESP_BODY" | head -c 200)"
  fi

  if [[ "$HTTP_STATUS" != "200" ]] && [[ "$HTTP_STATUS" != "202" ]]; then
    log "  WARNING: Trigger $i returned HTTP $HTTP_STATUS (workflow may still have run)"
  else
    log "  Trigger $i fired successfully ✓"
  fi

  # Wait between multiple triggers
  if [[ "$TRIGGER_COUNT" -gt 1 && "$i" -lt "$TRIGGER_COUNT" ]]; then
    log "  Waiting ${TRIGGER_GAP_SECONDS}s before next trigger..."
    sleep "$TRIGGER_GAP_SECONDS"
  fi
done

log "════════════════════════════════════════════"
log "  Run complete at $(date '+%H:%M:%S')"
log "════════════════════════════════════════════"
