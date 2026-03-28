"""
Async Pexels image + video search with simple TTL cache.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

import httpx

from app import config
from app.models.media import MediaRecord

logger = logging.getLogger(__name__)

MediaKind = Literal["image", "video"]

# (query_normalized, media_type, orientation) -> (monotonic_ts, records)
_cache: dict[tuple[str, str, str], tuple[float, list[MediaRecord]]] = {}


def _auth_headers() -> dict[str, str]:
    key = config.PEXELS_API_KEY
    if not key:
        raise RuntimeError("PEXELS_API_KEY is not set.")
    return {"Authorization": key}


def _best_image_url(photo: dict[str, Any]) -> tuple[str | None, int, int]:
    src = photo.get("src") or {}
    w = int(photo.get("width") or 0)
    h = int(photo.get("height") or 0)
    for key in ("original", "large2x", "landscape", "large", "portrait", "medium"):
        u = src.get(key)
        if u:
            return str(u), w, h
    return None, w, h


def _best_landscape_video_url(vid: dict[str, Any]) -> tuple[str | None, int, int, str | None]:
    """Prefer landscape MP4; fall back to largest file if none."""
    best_u: str | None = None
    best_area = 0
    best_w = best_h = 0
    photographer = vid.get("user", {}).get("name") if isinstance(vid.get("user"), dict) else None
    files = list(vid.get("video_files") or [])

    def consider(landscape_only: bool) -> None:
        nonlocal best_u, best_area, best_w, best_h
        for f in files:
            w = int(f.get("width") or 0)
            h = int(f.get("height") or 0)
            if w < 2 or h < 2:
                continue
            if landscape_only and w < h * 1.05:
                continue
            link = f.get("link")
            if not link:
                continue
            area = w * h
            if area > best_area:
                best_area = area
                best_u = str(link)
                best_w, best_h = w, h

    consider(landscape_only=True)
    if not best_u:
        best_area = 0
        consider(landscape_only=False)
    return best_u, best_w, best_h, str(photographer) if photographer else None


async def search_media(
    client: httpx.AsyncClient,
    query: str,
    media_type: MediaKind,
    *,
    orientation: str,
    per_page: int | None = None,
) -> tuple[list[MediaRecord], bool]:
    """
    Search Pexels for images or videos. Returns (records, cache_hit).
    """
    q = " ".join((query or "").split()).strip()
    if not q:
        return [], False

    orient = (orientation or config.DEFAULT_ORIENTATION).lower().strip()
    if orient not in ("landscape", "portrait", "square"):
        orient = "landscape"

    pp = per_page if per_page is not None else config.PEXELS_PER_PAGE
    cache_key = (q.lower(), media_type, orient)
    now = time.monotonic()
    hit = _cache.get(cache_key)
    if hit is not None:
        ts, cached = hit
        if now - ts < config.CACHE_TTL_SECONDS:
            return list(cached), True

    headers = _auth_headers()
    params: dict[str, Any] = {
        "query": q,
        "per_page": min(15, max(1, pp)),
        "orientation": orient,
    }

    if media_type == "image":
        url = config.PEXELS_IMAGE_SEARCH
    else:
        url = config.PEXELS_VIDEO_SEARCH

    try:
        r = await client.get(url, headers=headers, params=params)
        r.raise_for_status()
        payload = r.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Pexels HTTP error %s for %s: %s", e.response.status_code, q, e)
        return [], False
    except Exception as e:
        logger.warning("Pexels request failed for %s: %s", q, e)
        return [], False

    out: list[MediaRecord] = []

    if media_type == "image":
        for p in payload.get("photos") or []:
            u, w, h = _best_image_url(p)
            if not u:
                continue
            ph = p.get("photographer")
            area = max(1, w * h)
            out.append(
                MediaRecord(
                    type="image",
                    url=u,
                    photographer=str(ph) if ph else None,
                    width=w or None,
                    height=h or None,
                    score=float(area),
                )
            )
    else:
        for v in payload.get("videos") or []:
            u, w, h, ph = _best_landscape_video_url(v)
            if not u:
                continue
            area = max(1, w * h)
            out.append(
                MediaRecord(
                    type="video",
                    url=u,
                    photographer=ph,
                    width=w or None,
                    height=h or None,
                    score=float(area),
                )
            )

    out.sort(key=lambda m: m.score, reverse=True)
    _cache[cache_key] = (now, out)
    return out, False
