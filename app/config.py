"""Environment and limits for the travel media API."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
PEXELS_API_KEY: str | None = os.getenv("PEXELS_API_KEY")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_CHAT_COMPLETIONS_URL: str = "https://api.groq.com/openai/v1/chat/completions"

PEXELS_IMAGE_SEARCH: str = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_SEARCH: str = "https://api.pexels.com/videos/search"

# Max Pexels HTTP search calls per /travel/media request (each call = one orientation+type search).
MAX_PEXELS_CALLS: int = int(os.getenv("TRAVEL_MEDIA_MAX_PEXELS_CALLS", "20"))
MEDIA_PER_PLACE_MAX: int = int(os.getenv("TRAVEL_MEDIA_PER_PLACE_MAX", "10"))
PEXELS_PER_PAGE: int = int(os.getenv("TRAVEL_MEDIA_PEXELS_PER_PAGE", "5"))
DEFAULT_ORIENTATION: str = os.getenv("TRAVEL_MEDIA_ORIENTATION", "landscape")
CACHE_TTL_SECONDS: float = float(os.getenv("TRAVEL_MEDIA_CACHE_TTL", "300"))
OUTPUT_DIR: str = os.getenv("TRAVEL_MEDIA_OUTPUT_DIR", "output/travel_media_downloads")
