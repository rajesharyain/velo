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

# Groq chat model — fast, JSON-friendly
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Optional reel soundtrack
REEL_MUSIC_PATH: str | None = os.getenv("REEL_MUSIC_PATH")

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", PROJECT_ROOT / "output")).resolve()
CAROUSEL_DIR = OUTPUT_DIR / "carousel"
REELS_DIR = OUTPUT_DIR / "reels"

# Media preferences
CAROUSEL_SIZE = (1080, 1350)  # 4:5 feed
REEL_SIZE = (1080, 1920)  # 9:16
DESTINATION_COUNT_MIN = 3
DESTINATION_COUNT_MAX = 5
IMAGES_PER_DESTINATION = (2, 3)  # min, max
PEXELS_IMAGES_PAGE_SIZE = 15
PEXELS_VIDEOS_PAGE_SIZE = 10

# Reel: exactly this many still frames (downloaded photos), combined with FFmpeg
REEL_FRAME_COUNT = int(os.getenv("REEL_FRAME_COUNT", "5"))
# Total reel length in seconds (split evenly across frames)
REEL_TOTAL_SECONDS = float(os.getenv("REEL_TOTAL_SECONDS", "15"))


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


def ensure_output_dirs() -> None:
    """Create carousel and reels output folders."""
    CAROUSEL_DIR.mkdir(parents=True, exist_ok=True)
    REELS_DIR.mkdir(parents=True, exist_ok=True)
