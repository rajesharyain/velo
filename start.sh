#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# ── .env ──────────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "  Created .env from .env.example."
  echo "  Open .env and set GROQ_API_KEY and PEXELS_API_KEY, then re-run."
  echo ""
  exit 1
fi

if grep -qE '^GROQ_API_KEY=your_groq_api_key|^PEXELS_API_KEY=your_pexels_api_key' .env; then
  echo ""
  echo "  ERROR: .env still contains placeholder API keys."
  echo "  Set GROQ_API_KEY and PEXELS_API_KEY in .env, then re-run."
  echo ""
  exit 1
fi

PORT=8000

# ── Docker path (preferred — FFmpeg included in image) ────────────────────────
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  echo "→ Docker detected. Starting via docker compose (FFmpeg included)..."
  docker compose up --build -d
  echo ""
  echo "  Velo:  http://localhost:${PORT}"
  echo "  n8n:   http://localhost:5678"
  echo ""
  echo "  Logs:  docker compose logs -f velo"
  echo "  Stop:  docker compose down"
  echo ""
  (sleep 3 && open "http://localhost:${PORT}") &
  exit 0
fi

# ── Local fallback (requires Python 3.9+ and FFmpeg on PATH) ──────────────────
echo "  Docker not available — falling back to local Python environment."
echo ""

if [ ! -d .venv ]; then
  echo "→ Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "→ Installing / verifying dependencies..."
pip install -q -r requirements.txt

if ! command -v ffmpeg &>/dev/null; then
  echo ""
  echo "  WARNING: ffmpeg not found. Reel (MP4) creation will fail."
  echo "  Install with: brew install ffmpeg"
  echo ""
fi

echo "→ Starting Velo on http://127.0.0.1:${PORT}"
echo "  Press Ctrl-C to stop."
echo ""
(sleep 2 && open "http://127.0.0.1:${PORT}") &
PYTHONPATH="$(pwd)" python -m uvicorn velo_web:app --reload --host 127.0.0.1 --port "${PORT}"
