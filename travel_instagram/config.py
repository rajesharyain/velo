"""
Central configuration: environment variables and output paths.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# API keys (required for full pipeline)
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
PEXELS_API_KEY: str | None = os.getenv("PEXELS_API_KEY")

# Instagram Graph API credentials (required for publishing)
IG_USER_ID: str | None = os.getenv("IG_USER_ID")
IG_ACCESS_TOKEN: str | None = os.getenv("IG_ACCESS_TOKEN")

# Public base URL used to build HTTPS video URLs for Meta's servers.
# Must be set to an externally reachable URL, e.g. https://abc123.ngrok.io
PUBLIC_APP_BASE_URL: str = (os.getenv("PUBLIC_APP_BASE_URL", "") or "").strip().rstrip("/")

# Groq chat model — fast, JSON-friendly
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Optional reel soundtrack (used when no track is chosen in the UI / CLI)
REEL_MUSIC_PATH: str | None = os.getenv("REEL_MUSIC_PATH")

# Jamendo API client ID — enables auto-fetching royalty-free music for reels
# Free signup at https://devportal.jamendo.com/
JAMENDO_CLIENT_ID: str | None = os.getenv("JAMENDO_CLIENT_ID")

# MCP reel generator: Excel with destination/origin/prices
TRAVEL_PRICES_EXCEL_PATH: str | None = os.getenv("TRAVEL_PRICES_EXCEL_PATH")

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MUSIC_LIBRARY_DIR = PROJECT_ROOT / "music"
MUSIC_AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"})
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", PROJECT_ROOT / "output")).resolve()
MCP_REELS_DIR = OUTPUT_DIR / "mcp_reels"
CAROUSEL_DIR = OUTPUT_DIR / "carousel"
REELS_DIR = OUTPUT_DIR / "reels"
# Saved Reels AD sessions (downloaded media + session.json for reopening in the UI)
AD_REELS_LIBRARY_DIR = OUTPUT_DIR / "ad_reels_library"

# Media preferences — carousel JPEGs (9:16, same as Reels / IG vertical feed)
# Dimensions follow REEL_WIDTH × REEL_HEIGHT (defined just below)
# Final carousel slide primary text (replaces on-image Groq CTA such as “Book your dream trip”)
# Newlines become separate paragraphs (stacked lines) for long closing copy
CAROUSEL_CLOSING_TEXT: str = (
    os.getenv(
        "CAROUSEL_CLOSING_TEXT",
        "Explore more\nvisit budgetwing.com\nfor cheap flights.",
    )
    or "Explore more\nvisit budgetwing.com\nfor cheap flights."
).strip()
# Legacy: small upward nudge when a slide has primary text only (0 = off)
CAROUSEL_TEXT_BIAS_UP_RATIO: float = float(os.getenv("CAROUSEL_TEXT_BIAS_UP_RATIO", "0"))
# Reels / IG UI safe zones on carousel JPEGs (ratios of slide height, 0–0.35)
REEL_TEXT_SAFE_TOP_RATIO: float = float(os.getenv("REEL_TEXT_SAFE_TOP_RATIO", "0.055"))
REEL_TEXT_SAFE_BOTTOM_RATIO: float = float(os.getenv("REEL_TEXT_SAFE_BOTTOM_RATIO", "0.20"))
# Primary (“subject”) is vertically centered between top safe area and this y-ratio
REEL_PRIMARY_ZONE_END_RATIO: float = float(os.getenv("REEL_PRIMARY_ZONE_END_RATIO", "0.44"))
# Captions start in this lower band (middle-lower, not flush to bottom)
REEL_CAPTION_ZONE_START_RATIO: float = float(os.getenv("REEL_CAPTION_ZONE_START_RATIO", "0.48"))
# Extra horizontal inset vs reel-safe width (0.9–1.0) for right-side action icons
REEL_TEXT_SIDE_INSET_RATIO: float = float(os.getenv("REEL_TEXT_SIDE_INSET_RATIO", "0.93"))
# On-slide styling for brand domain (R, G, B)
BRAND_DOMAIN_RGB: tuple[int, int, int] = (
    int(os.getenv("BRAND_DOMAIN_R", "64")),
    int(os.getenv("BRAND_DOMAIN_G", "196")),
    int(os.getenv("BRAND_DOMAIN_B", "255")),
)
# Minimum long edge (px) when choosing Pexels photos (0 = no filter; try 1400+ for stricter HQ)
PEXELS_MIN_PHOTO_EDGE: int = max(0, int(os.getenv("PEXELS_MIN_PHOTO_EDGE", "0")))
# Reels: portrait 9:16 (Instagram Reels). Override with REEL_WIDTH × REEL_HEIGHT if needed.
_REEL_W = max(360, int(os.getenv("REEL_WIDTH", "1080")))
_REEL_H = max(640, int(os.getenv("REEL_HEIGHT", "1920")))
REEL_SIZE = (_REEL_W, _REEL_H)
CAROUSEL_SIZE = REEL_SIZE
# Footer URL below the caption when not already in slide text (avoids duplicate on closing slide).
REEL_BRAND_TEXT: str = (os.getenv("REEL_BRAND_TEXT", "www.budgetwing.com") or "").strip()
DESTINATION_COUNT_MIN = 3
DESTINATION_COUNT_MAX = 5
# "Top N places" / ranked lists from Groq (capped for API size and Pexels cost)
DESTINATION_REQUEST_MAX = max(5, int(os.getenv("DESTINATION_REQUEST_MAX", "15")))
IMAGES_PER_DESTINATION = (2, 3)  # min, max
PEXELS_IMAGES_PAGE_SIZE = 15
PEXELS_VIDEOS_PAGE_SIZE = 10

# Reel: exactly this many still frames (downloaded photos), combined with FFmpeg
REEL_FRAME_COUNT = int(os.getenv("REEL_FRAME_COUNT", "5"))
# Legacy cap when REEL_SECONDS_PER_SLIDE is not used alone; still honored as upper hint.
REEL_TOTAL_SECONDS = float(os.getenv("REEL_TOTAL_SECONDS", "15"))
# Travel carousel reel: total length ≈ n_slides × per-slide seconds (clamped), not a fixed ~15–20s.
REEL_SECONDS_PER_SLIDE = float(os.getenv("REEL_SECONDS_PER_SLIDE", "2.9"))
REEL_MIN_TOTAL_SECONDS = float(os.getenv("REEL_MIN_TOTAL_SECONDS", "8"))
REEL_MAX_TOTAL_SECONDS = float(os.getenv("REEL_MAX_TOTAL_SECONDS", "78"))
# xfade between segments — higher values = slower, more visible transitions
REEL_XFADE_MIN_SECONDS = float(os.getenv("REEL_XFADE_MIN_SECONDS", "0.42"))
REEL_XFADE_MAX_SECONDS = float(os.getenv("REEL_XFADE_MAX_SECONDS", "0.95"))
REEL_XFADE_SEGMENT_RATIO = float(os.getenv("REEL_XFADE_SEGMENT_RATIO", "0.34"))

# InstaPost FFmpeg reel: duration range when caller does not set a total
INSTAPOST_REEL_MIN_SECONDS = float(os.getenv("INSTAPOST_REEL_MIN_SECONDS", "22"))
INSTAPOST_REEL_MAX_SECONDS = float(os.getenv("INSTAPOST_REEL_MAX_SECONDS", "52"))

# Carousel slide text: larger type + semi-transparent panel behind the text block
CAROUSEL_TITLE_FONT_SCALE = float(os.getenv("CAROUSEL_TITLE_FONT_SCALE", "1.14"))
CAROUSEL_BODY_FONT_SCALE = float(os.getenv("CAROUSEL_BODY_FONT_SCALE", "1.10"))
CAROUSEL_TEXT_PANEL_ALPHA = int(max(0, min(255, int(os.getenv("CAROUSEL_TEXT_PANEL_ALPHA", "218")))))


def resolve_ffmpeg_executable() -> str | None:
    """
    If FFMPEG_PATH is set, resolve ffmpeg.exe.

    Accepts:
    - Full path to ffmpeg.exe (or `ffmpeg` on Unix)
    - Folder containing ffmpeg.exe (e.g. Windows build root)
    - Folder with bin/ffmpeg.exe (typical Windows zip layout)
    """
    raw = os.getenv("FFMPEG_PATH", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_file():
        return str(p.resolve())
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for sub in (p / exe_name, p / "bin" / exe_name):
        if sub.is_file():
            return str(sub.resolve())
    return None


def list_music_tracks() -> list[dict[str, str]]:
    """
    Audio files under ``music/`` (recursive), sorted by relative path for dropdowns.

    Each item: ``{"id": "<posix relative path>", "label": "<same or basename>"}``.
    """
    base = MUSIC_LIBRARY_DIR.resolve()
    if not base.is_dir():
        return []
    out: list[dict[str, str]] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in MUSIC_AUDIO_EXTENSIONS:
            continue
        rel = p.relative_to(base)
        rid = rel.as_posix()
        out.append({"id": rid, "label": rid})
    return out


def resolve_reel_music(music_track_id: str | None) -> Path | None:
    """
    Resolve which audio file to mux onto the reel.

    - ``music_track_id`` is ``None`` (omitted): use ``REEL_MUSIC_PATH`` if it exists,
      else the first file from ``list_music_tracks()``, else no music.
    - ``""``, ``"__none__"``, or ``"none"`` (case-insensitive): no music.
    - Otherwise: must be a relative path under ``music/`` (as returned by
      ``list_music_tracks()`` ``id``). Path traversal outside ``music/`` is rejected.
    """
    base = MUSIC_LIBRARY_DIR.resolve()

    def env_path() -> Path | None:
        raw = (REEL_MUSIC_PATH or "").strip()
        if not raw:
            return None
        p = Path(raw).expanduser().resolve()
        return p if p.is_file() else None

    def first_in_library() -> Path | None:
        tracks = list_music_tracks()
        if not tracks:
            return None
        cand = (base / tracks[0]["id"]).resolve()
        return cand if cand.is_file() else None

    if music_track_id is None:
        return env_path() or first_in_library()

    tid = str(music_track_id).strip()
    if not tid or tid.lower() in ("__none__", "none"):
        return None

    cand = (base / Path(tid)).resolve()
    try:
        cand.relative_to(base)
    except ValueError:
        return None
    if cand.is_file() and cand.suffix.lower() in MUSIC_AUDIO_EXTENSIONS:
        return cand
    return None


def ensure_output_dirs() -> None:
    """Create carousel, reels, and optional ``music/`` drop folder."""
    CAROUSEL_DIR.mkdir(parents=True, exist_ok=True)
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    AD_REELS_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
