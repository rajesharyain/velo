from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

from travel_instagram import config

logger = logging.getLogger(__name__)

PEXELS_IMAGE_SEARCH = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_SEARCH = "https://api.pexels.com/videos/search"


@dataclass
class PexelsInstaVideo:
    url: str
    width: int
    height: int
    duration: float | None
    id: str | None = None


@dataclass
class PexelsInstaBundle:
    destination: str
    query: str
    videos: list[PexelsInstaVideo] = field(default_factory=list)
    images: list[str] = field(default_factory=list)  # URL list


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": api_key}


def _best_photo_src_url(p: dict[str, Any]) -> str | None:
    src = p.get("src") or {}
    for key in ("original", "large2x", "portrait", "large", "medium"):
        u = src.get(key)
        if u:
            return str(u)
    return None


def _best_portrait_video_file(v: dict[str, Any]) -> PexelsInstaVideo | None:
    files = v.get("video_files") or []
    duration = v.get("duration")
    duration_f = float(duration) if duration is not None else None
    for f in files:
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        if w < 1 or h < 1:
            continue
        if h < w * 0.9:
            continue
        link = f.get("link")
        if not link:
            continue
        return PexelsInstaVideo(
            url=str(link),
            width=w,
            height=h,
            duration=duration_f,
            id=v.get("id"),
        )
    return None


def _video_score(vid: PexelsInstaVideo) -> float:
    qbonus = 1.0
    if vid.height >= 1500:
        qbonus += 1.0
    return float(vid.width * vid.height) * qbonus


def _score_image_urls(photos: list[dict[str, Any]], exclude_urls: set[str]) -> list[str]:
    min_edge = int(getattr(config, "PEXELS_MIN_PHOTO_EDGE", 0) or 0)
    scored: list[tuple[int, str]] = []
    for p in photos:
        w = int(p.get("width") or 0)
        h = int(p.get("height") or 0)
        if min_edge > 0 and w > 0 and h > 0 and max(w, h) < min_edge:
            continue
        url = _best_photo_src_url(p)
        if not url or url in exclude_urls:
            continue
        scored.append((max(1, w * h), url))
    scored.sort(key=lambda t: t[0], reverse=True)
    candidates = [u for _, u in scored]
    random.shuffle(candidates)
    out: list[str] = []
    seen: set[str] = set()
    for u in candidates:
        if u in seen or u in exclude_urls:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= 50:
            break
    return out


async def fetch_insta_media(
    destination_name: str,
    *,
    api_key: str | None = None,
    video_count: int = 4,
    image_count: int = 3,
    pexels_search_query: str | None = None,
    exclude_video_urls: set[str] | None = None,
    exclude_image_urls: set[str] | None = None,
) -> PexelsInstaBundle:
    """
    Fetch multiple portrait videos and portrait images for a single destination.
    """
    key = api_key or config.PEXELS_API_KEY
    if not key:
        raise RuntimeError("PEXELS_API_KEY is not set. Add it to your environment or .env file.")

    dest = destination_name.strip()
    if not dest:
        raise ValueError("destination_name must be non-empty.")

    query = (pexels_search_query or "").strip() or dest
    exclude_v = exclude_video_urls or set()
    exclude_i = exclude_image_urls or set()

    headers = _auth_headers(key)
    bundle = PexelsInstaBundle(destination=dest, query=query)

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Images first (often cheaper and gives fallback).
        try:
            ir = await client.get(
                PEXELS_IMAGE_SEARCH,
                headers=headers,
                params={
                    "query": query,
                    "per_page": max(config.PEXELS_IMAGES_PAGE_SIZE, 24),
                    "orientation": "portrait",
                },
            )
            ir.raise_for_status()
            idata = ir.json()
            photos = idata.get("photos") or []
            bundle.images = _score_image_urls(photos, exclude_i)[:image_count]
        except httpx.HTTPError:
            logger.warning("Pexels image search failed for %r", query)

        # Videos (portrait).
        try:
            vr = await client.get(
                PEXELS_VIDEO_SEARCH,
                headers=headers,
                params={
                    "query": query,
                    "per_page": max(config.PEXELS_VIDEOS_PAGE_SIZE, 20),
                    "orientation": "portrait",
                },
            )
            vr.raise_for_status()
            vdata = vr.json()
            vids = vdata.get("videos") or []

            picked: list[PexelsInstaVideo] = []
            for v in vids:
                bv = _best_portrait_video_file(v)
                if not bv:
                    continue
                if bv.url in exclude_v:
                    continue
                picked.append(bv)

            picked.sort(key=_video_score, reverse=True)
            # Shuffle top-N so consecutive runs feel less repetitive.
            top = picked[: max(10, video_count * 2)]
            random.shuffle(top)

            seen: set[str] = set()
            out: list[PexelsInstaVideo] = []
            for pv in top:
                if pv.url in seen or pv.url in exclude_v:
                    continue
                seen.add(pv.url)
                out.append(pv)
                if len(out) >= video_count:
                    break
            bundle.videos = out
        except httpx.HTTPError:
            logger.warning("Pexels video search failed for %r", query)

    if not bundle.videos:
        logger.warning("No Pexels portrait videos found for query=%r", query)
    if not bundle.images:
        logger.warning("No Pexels portrait images found for query=%r", query)

    return bundle

