"""
Pexels REST client: portrait images and vertical videos per destination keyword.
"""

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
class PexelsMediaBundle:
    """Resolved URLs for one destination."""

    destination: str  # display name from Groq
    query: str  # exact string sent to Pexels (from pexels_search_query or destination)
    image_urls: list[str] = field(default_factory=list)
    video: dict[str, Any] | None = None  # {url, width, height, duration}


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": api_key}


def _pick_image_urls(photos: list[dict[str, Any]], count: int) -> list[str]:
    """Prefer large portrait-friendly sources; randomize order then take `count`."""
    candidates: list[str] = []
    for p in photos:
        src = p.get("src") or {}
        # Prefer largest available for overlays
        url = (
            src.get("original")
            or src.get("large2x")
            or src.get("large")
            or src.get("medium")
        )
        if url:
            candidates.append(url)
    random.shuffle(candidates)
    return candidates[:count]


def _best_portrait_video(videos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Choose a vertical file from Pexels video objects.
    Prefer height >= width and quality hd/sd with highest resolution.
    """
    best: dict[str, Any] | None = None
    best_score = -1.0

    for vid in videos:
        files = vid.get("video_files") or []
        for f in files:
            w = int(f.get("width") or 0)
            h = int(f.get("height") or 0)
            if w < 1 or h < 1:
                continue
            if h < w * 0.9:  # not sufficiently vertical
                continue
            link = f.get("link")
            if not link:
                continue
            q = str(f.get("quality") or "").lower()
            q_bonus = 2.0 if q == "hd" else 1.0 if q == "sd" else 0.5
            score = h * w * q_bonus + (h / max(w, 1)) * 500
            if score > best_score:
                best_score = score
                best = {
                    "url": link,
                    "width": w,
                    "height": h,
                    "duration": float(vid.get("duration") or 0),
                    "id": vid.get("id"),
                }

    return best


def fetch_media_for_destination(
    destination_name: str,
    api_key: str | None = None,
    image_count: int | None = None,
    include_video: bool = False,
    pexels_search_query: str | None = None,
) -> PexelsMediaBundle:
    """
    Search Pexels for images (portrait). Optionally fetch a vertical video.

    ``pexels_search_query`` should be Groq-generated (place + scenery keywords).
    If omitted, ``destination_name`` is used as the search string.

    `image_count` defaults to a random value between IMAGES_PER_DESTINATION.
    When ``include_video`` is False (default), no video API call is made.
    """
    key = api_key or config.PEXELS_API_KEY
    if not key:
        raise RuntimeError(
            "PEXELS_API_KEY is not set. Add it to your environment or .env file."
        )

    low, high = config.IMAGES_PER_DESTINATION
    n_img = image_count if image_count is not None else random.randint(low, high)
    name = destination_name.strip()
    query = (pexels_search_query or "").strip() or name

    headers = _auth_headers(key)
    bundle = PexelsMediaBundle(destination=name, query=query)

    with httpx.Client(timeout=60.0) as client:
        # Images — portrait orientation for carousel/reel crops
        ir = client.get(
            PEXELS_IMAGE_SEARCH,
            headers=headers,
            params={
                "query": query,
                "per_page": config.PEXELS_IMAGES_PAGE_SIZE,
                "orientation": "portrait",
            },
        )
        ir.raise_for_status()
        idata = ir.json()
        photos = idata.get("photos") or []
        if not photos:
            # Fallback: any orientation
            ir2 = client.get(
                PEXELS_IMAGE_SEARCH,
                headers=headers,
                params={
                    "query": query,
                    "per_page": config.PEXELS_IMAGES_PAGE_SIZE,
                },
            )
            ir2.raise_for_status()
            photos = ir2.json().get("photos") or []

        bundle.image_urls = _pick_image_urls(photos, n_img)
        if not bundle.image_urls and query != name:
            logger.warning(
                "No Pexels images for scenery query; retrying with destination name only: %s",
                name,
            )
            ir3 = client.get(
                PEXELS_IMAGE_SEARCH,
                headers=headers,
                params={
                    "query": name,
                    "per_page": config.PEXELS_IMAGES_PAGE_SIZE,
                    "orientation": "portrait",
                },
            )
            ir3.raise_for_status()
            photos3 = ir3.json().get("photos") or []
            if not photos3:
                ir4 = client.get(
                    PEXELS_IMAGE_SEARCH,
                    headers=headers,
                    params={
                        "query": name,
                        "per_page": config.PEXELS_IMAGES_PAGE_SIZE,
                    },
                )
                ir4.raise_for_status()
                photos3 = ir4.json().get("photos") or []
            bundle.image_urls = _pick_image_urls(photos3, n_img)
            if bundle.image_urls:
                bundle = PexelsMediaBundle(
                    destination=name,
                    query=name,
                    image_urls=bundle.image_urls,
                    video=bundle.video,
                )

        if not bundle.image_urls:
            logger.warning("No Pexels images for query: %s", query)

        if include_video:
            vr = client.get(
                PEXELS_VIDEO_SEARCH,
                headers=headers,
                params={
                    "query": query,
                    "per_page": config.PEXELS_VIDEOS_PAGE_SIZE,
                    "orientation": "portrait",
                },
            )
            vr.raise_for_status()
            vdata = vr.json()
            vids = vdata.get("videos") or []
            bundle.video = _best_portrait_video(vids)
            if bundle.video is None and vids:
                logger.debug("No portrait video file for %s.", query)

    logger.debug("Pexels image search query=%r (destination=%r)", query, name)

    return bundle
