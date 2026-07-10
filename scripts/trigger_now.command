#!/usr/bin/env bash
# Velo — one-click manual trigger.
# Double-click in Finder to fire the n8n workflow immediately.

WEBHOOK="http://localhost:5678/webhook/velo-daily-run"
N8N_HEALTH="http://localhost:5678/healthz"
VELO_HEALTH="http://localhost:8000/api/health"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

clear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Velo — Manual Trigger"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Ensure Docker is running ──────────────────────────────────────────────────
if ! docker info >/dev/null 2>&1; then
  echo "  Docker not running — starting Docker Desktop..."
  open -a Docker
  for i in $(seq 1 18); do
    sleep 5
    docker info >/dev/null 2>&1 && break
    echo "  Waiting for Docker... ($((i * 5))s)"
  done
  if ! docker info >/dev/null 2>&1; then
    echo "  ERROR: Docker did not start. Aborting."
    read -r -p "Press Enter to close..."; exit 1
  fi
  echo "  Docker ready ✓"
fi

# ── Ensure containers are up ──────────────────────────────────────────────────
COMPOSE="$HOME/Library/Application Support/Velo/docker-compose.yml"
if [[ -f "$COMPOSE" ]]; then
  echo "  Starting containers..."
  docker compose -f "$COMPOSE" up -d >/dev/null 2>&1
fi

# ── Wait for velo + n8n ───────────────────────────────────────────────────────
echo "  Waiting for services..."
for i in $(seq 1 30); do
  curl -sf "$VELO_HEALTH" >/dev/null 2>&1 && break
  sleep 3
done
for i in $(seq 1 20); do
  curl -sf "$N8N_HEALTH" >/dev/null 2>&1 && break
  sleep 3
done

if ! curl -sf "$N8N_HEALTH" >/dev/null 2>&1; then
  echo "  ERROR: n8n not reachable. Is Docker running?"
  read -r -p "Press Enter to close..."; exit 1
fi

echo "  Services ready ✓"
echo ""

# ── Fire the webhook ──────────────────────────────────────────────────────────
echo "  Triggering workflow..."
RESP=$(curl -s -w "\n%{http_code}" -X POST "$WEBHOOK" \
  -H "Content-Type: application/json" \
  -d '{"source":"manual"}')

HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | head -1)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "202" ]]; then
  echo "  ✅  Workflow started! (HTTP $HTTP_CODE)"
  echo "      $BODY"
  echo ""
  echo "  Check n8n UI → http://localhost:5678"
  echo "  to watch execution in real time."
else
  echo "  ❌  Failed (HTTP $HTTP_CODE)"
  echo "      $BODY"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -r -p "Press Enter to close..."
