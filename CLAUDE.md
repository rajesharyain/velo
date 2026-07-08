# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Velo is a travel Instagram content generator. It uses **Groq** (LLM) to generate travel copy and **Pexels** for media, then builds:
- **Carousels**: 5–10 JPEG slides at 1080×1920 with titles, destination captions, hashtags, and CTA
- **Reels**: 1080×1920 MP4 assembled from carousel slides via FFmpeg with optional background music

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # or .\.venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
cp .env.example .env               # then set GROQ_API_KEY and PEXELS_API_KEY
```

**External dependency**: FFmpeg must be on `PATH`, or set `FFMPEG_PATH` in `.env` to the binary/folder.

Optional `.env` keys (beyond `GROQ_API_KEY` and `PEXELS_API_KEY`):
- `GROQ_MODEL` — default `llama-3.3-70b-versatile`
- `REEL_MUSIC_PATH` — fallback MP3/WAV when no track is selected in UI
- `OUTPUT_DIR` — override output root (default `./output`)
- `IG_USER_ID` + `IG_ACCESS_TOKEN` — enable Instagram Reels publishing via `instagram_service.py`
- `FB_PAGE_ID` + `FB_PAGE_ACCESS_TOKEN` — enable Facebook video publishing via `facebook_service.py`
- `CAROUSEL_CLOSING_TEXT` — last-slide CTA copy (default: `"Explore more\nvisit budgetwing.com\nfor cheap flights."`)
- `REEL_BRAND_TEXT` — footer URL stamped on slides (default: `www.budgetwing.com`)

## Running the web server

```bash
python -m uvicorn velo_web:app --reload --host 127.0.0.1 --port 8000
# or on Windows: .\run_web.ps1
```

Always use `velo_web:app` as the entry point (not `travel_instagram.web_app:app` directly) — `velo_web.py` ensures the repo root is first on `sys.path` so the local package is always resolved correctly.

## CLI

```bash
python -m travel_instagram.cli --theme "hidden beaches in Europe"
python -m travel_instagram.cli --batch themes.txt      # one theme per line, # = comment
python -m travel_instagram.cli -t "budget travel Asia" -v   # verbose FFmpeg
```

## MCP server

```bash
python -m travel_instagram.mcp_server    # JSON-RPC 2.0 over stdio
```

## Tests

```bash
python -m pytest tests/
python -m pytest tests/test_ad_reels_page.py   # single file
```

Tests use FastAPI's `TestClient` (from `httpx`) and standard `unittest`. There are no mocks — the tests hit the real FastAPI app.

## n8n automation

`docker compose up -d` starts n8n at http://localhost:5678. The n8n workflow at `n8n/workflows/velo-to-instagram.json` automates posting generated media to Instagram. Velo's API must be running separately (it is not in the compose file).

## Architecture

### Two module trees with distinct responsibilities

**`travel_instagram/`** — the core package, handles all generation:

| Module | Role |
|--------|------|
| `config.py` | Single source of truth for all env vars and output paths |
| `groq_service.py` | Groq API calls — generates destination JSON (names, `scape_types`, `vibe`, `pexels_search_query`), blog HTML, reel scripts |
| `pexels_service.py` | Pexels image/video search; falls back from `pexels_search_query` to place name if empty |
| `media_processor.py` | PIL-based carousel slide rendering; raw RGB24 → FFmpeg libx264 reel encoding |
| `pipeline.py` | Main orchestrator: Groq → Pexels → downloads → carousel → reel → `summary.json` |
| `cli.py` | argparse CLI wrapping `pipeline.run_pipeline` and `run_batch` |
| `manual_reel_builder.py` | Build reels from user-uploaded files or URL items |
| `manual_reel_autofill.py` | Auto-fetch Pexels media for the manual reel UI |
| `mcp_reel_tool.py` | Generates price reels from Excel + Pexels; used by both MCP server and web UI |
| `mcp_server.py` | Minimal JSON-RPC 2.0 stdio server exposing `generate_travel_reel` |
| `reels_catalog.py` | Scans `output/carousel/*/summary.json` to build the reels library list |
| `web_app.py` | FastAPI app — all routes and API endpoints |
| `instagram_service.py` | Instagram Graph API v21.0 publisher (`publish_reel`); requires `IG_USER_ID` + `IG_ACCESS_TOKEN` in `.env` |
| `facebook_service.py` | Facebook Graph API v21.0 publisher (`publish_page_video`); requires `FB_PAGE_ID` + `FB_PAGE_ACCESS_TOKEN` in `.env` |
| `instagram_post_export.py` | Exports carousel slides to Instagram feed dimensions (1:1, 4:5, 1.91:1) under `<run_dir>/instagram_feed/` |
| `instapost/` | Sub-package for the InstaPost feature — see modules below |

`instapost/` sub-package:

| Module | Role |
|--------|------|
| `groq_script_service.py` | Groq generates N variation reel scripts + vibe place list |
| `pexels_service.py` | Async Pexels search scoped to InstaPost (separate from top-level service) |
| `media_downloader.py` | Downloads Pexels clips into `output/instapost/<run_id>/` |
| `ffmpeg_reel_builder.py` | Assembles one MP4 per script variation with FFmpeg |
| `pipeline.py` | Orchestrates the full InstaPost flow end-to-end |
| `router.py` | FastAPI router mounted at `/instapost` and `/instapost/api/generate` |

**`app/`** — a separate aggregator layer used exclusively by the `/ad-reels` UI:

| Module | Role |
|--------|------|
| `config.py` | Env vars specific to the ad-reels aggregator (separate from `travel_instagram/config.py`) |
| `services/aggregator.py` | `aggregate_travel_media()`: Groq → 5 structured places → up to 20 parallel Pexels calls → deduplicated media per place |
| `services/groq_service.py` | Async Groq call to produce structured place objects |
| `services/pexels_service.py` | Async Pexels search with in-memory TTL cache |
| `models/place.py` | Pydantic models for the aggregator's request/response shapes |

### Key data flow

**Theme pipeline** (`/api/generate`):
`theme` → `groq_service.generate_travel_content()` → destinations JSON → per-destination Pexels image fetch → `media_processor.build_carousel_slides()` → `media_processor.build_reel_from_images()` → `summary.json`

**Ad-reels pipeline** (`/api/ad-reels/travel-media`):
`query` → `app.services.aggregator.aggregate_travel_media()` → Groq produces 5 structured places → up to 20 parallel Pexels calls (images + videos) → merged, deduplicated media returned as JSON for the browser UI

**InstaPost pipeline** (`/instapost/api/generate`):
`destination_query` → `instapost.groq_script_service` generates N variation scripts + vibe places → parallel Pexels fetches per place → `instapost.ffmpeg_reel_builder` assembles one reel per script variation

### Output directory layout

All output goes under `OUTPUT_DIR` (default: `./output/`), served at `/media/` by FastAPI's `StaticFiles`:

```
output/
  carousel/<run_id>/carousel/slide_01.jpg ...   # carousel slides
  carousel/<run_id>/downloads/                   # cached Pexels files
  carousel/<run_id>/summary.json
  reels/reel_<slug>.mp4
  mcp_reels/
  ad_reels_library/<session_id>/session.json    # saved ad-reels sessions + downloaded clips
  instapost/<run_id>/
  manual_reels/
```

### Music library

Drop MP3/WAV files into `music/` — the web UI populates a dropdown via `GET /api/music-tracks`. The `config.resolve_reel_music()` function resolves the selected track, falling back to `REEL_MUSIC_PATH` env var, then the first file in `music/`, then silence.

### Web routes summary

| Path | Template |
|------|----------|
| `/` | `index.html` — theme carousel/reel generator |
| `/reels` | `reels.html` — reel library |
| `/ad-reels` | `reels_ad.html` — Groq places + Pexels media picker |
| `/ad-reels/library` | `ad_reels_library.html` — saved sessions |
| `/mcp-reels` | `mcp_reels.html` — price reel generator |
| `/upload-reel` | `upload_reel.html` — manual reel from uploads |
| `/instapost` | `instapost.html` — InstaPost multi-variation reels |
| `/travel-blog` | `travel_blog.html` — Groq blog HTML generator |
| `/carousel/{run_id}` | `carousel_run.html` — slide viewer for one pipeline run |

Key API endpoints (POST/GET, not page routes):

| Endpoint | Purpose |
|----------|---------|
| `POST /api/generate` | Runs the theme pipeline; returns `summary.json` + `*_url` fields |
| `POST /api/blog/generate` | Generates travel blog HTML via Groq |
| `POST /api/ad-reels/travel-media` | Aggregator pipeline → structured places + Pexels media |
| `GET/POST /api/ad-reels/library/...` | CRUD for saved ad-reels sessions |
| `POST /api/mcp-reels/generate` | Price reel from Excel + Pexels |
| `POST /api/upload-reel/generate` | Manual reel from uploaded files or URL items |
| `POST /api/upload-reel/autofill` | Auto-fetch Pexels for manual reel slots |
| `GET /api/reels` | Reels library list |
| `GET /api/reels/export.xlsx` | Export reels library as Excel |
| `GET /api/music-tracks` | List available tracks from `music/` |
| `GET /api/health` | Health check |
