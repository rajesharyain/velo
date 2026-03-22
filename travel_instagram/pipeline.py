"""
End-to-end orchestration: Groq → Pexels → downloads → carousel + reel → JSON summary.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from travel_instagram import config
from travel_instagram import groq_service
from travel_instagram import media_processor
from travel_instagram import pexels_service

logger = logging.getLogger(__name__)


def _reel_carousel_slide_paths(slide_paths: list[Path], count: int) -> list[Path]:
    """
    Use rendered carousel JPEGs for the reel — **unique files only**, no cycling duplicates.

    If there are fewer slides than ``count``, the reel uses that many distinct frames
    (each gets a longer share of ``REEL_TOTAL_SECONDS``).
    """
    pool = [p for p in slide_paths if p.is_file()]
    if not pool:
        raise RuntimeError("No carousel slide files exist for reel.")
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in pool:
        k = str(p.resolve())
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    c = max(1, min(count, len(uniq)))
    return uniq[:c]


def _normalize_music_selection(music_track_id: str | None) -> str | None:
    """``None`` / empty / ``__auto__`` → let ``resolve_reel_music`` use env + library defaults."""
    if music_track_id is None:
        return None
    s = str(music_track_id).strip()
    if not s or s == "__auto__":
        return None
    return s


def _slug(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (s[:max_len] or "theme") + f"-{uuid.uuid4().hex[:8]}"


def run_pipeline(theme: str, music_track_id: str | None = None) -> dict[str, Any]:
    """
    Generate carousel JPEGs and reel MP4 for one theme.

    ``music_track_id``: relative path under ``music/`` (from the web dropdown), or
    ``"__none__"`` for no music, or ``None`` to use ``REEL_MUSIC_PATH`` / first library file.

    Returns a JSON-serializable summary including file paths and metadata.
    """
    config.ensure_output_dirs()
    theme = theme.strip()
    if not theme:
        raise ValueError("Theme must be non-empty.")

    run_slug = _slug(theme)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + run_slug
    work = config.CAROUSEL_DIR / run_id
    dl = work / "downloads"
    work.mkdir(parents=True, exist_ok=True)
    dl.mkdir(parents=True, exist_ok=True)

    logger.info("Groq: generating content for theme %r", theme)
    content = groq_service.generate_travel_content(theme)
    destinations = list(content.get("destinations") or [])
    if not destinations:
        raise RuntimeError("Groq returned no destinations; try a different theme or model.")

    bundles: list[pexels_service.PexelsMediaBundle] = []
    image_paths_by_dest: list[list[Path]] = []
    used_pexels_urls: set[str] = set()

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for i, dest in enumerate(destinations):
            name = str(dest.get("destination", ""))
            pq = str(dest.get("pexels_search_query") or "").strip()
            logger.info(
                "Pexels: images for %r (query=%r, scape_types=%s)",
                name,
                pq or name,
                dest.get("scape_types"),
            )
            b = pexels_service.fetch_media_for_destination(
                name,
                include_video=False,
                pexels_search_query=pq or None,
                exclude_image_urls=used_pexels_urls,
            )
            bundles.append(b)

            imgs: list[Path] = []
            for j, url in enumerate(b.image_urls):
                if url in used_pexels_urls:
                    continue
                ext = ".jpg"
                low = url.lower()
                if ".png" in low:
                    ext = ".png"
                p = dl / f"img_{i}_{len(imgs)}{ext}"
                try:
                    media_processor.download_binary(url, p, client=client)
                    used_pexels_urls.add(url)
                    imgs.append(p)
                except Exception as e:
                    logger.warning("Image download failed (%s): %s", url[:80], e)

            image_paths_by_dest.append(imgs)

    if not any(image_paths_by_dest):
        raise RuntimeError("No images could be downloaded for any destination.")

    carousel_dir = work / "carousel"
    carousel_dir.mkdir(parents=True, exist_ok=True)
    slide_paths = media_processor.build_carousel_slides(
        carousel_dir,
        content,
        image_paths_by_dest,
        reel_theme=theme,
    )

    reel_name = f"reel_{run_slug}.mp4"
    reel_path = config.REELS_DIR / reel_name
    reel_work = work / "reel_build"
    reel_work.mkdir(parents=True, exist_ok=True)
    reel_images = _reel_carousel_slide_paths(slide_paths, config.REEL_FRAME_COUNT)
    music_path = config.resolve_reel_music(_normalize_music_selection(music_track_id))
    media_processor.build_reel_from_images(
        reel_work,
        reel_images,
        reel_path,
        music_path=music_path,
    )

    summary: dict[str, Any] = {
        "run_id": run_id,
        "theme": theme,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "content": {
            "hook": content.get("hook"),
            "cta": content.get("cta"),
            "hashtags": content.get("hashtags"),
            "destinations": destinations,
        },
        "media": [],
        "outputs": {
            "carousel_slides": [str(p.resolve()) for p in slide_paths],
            "reel_video": str(reel_path.resolve()),
            "reel_source_carousel_slides": [str(p.resolve()) for p in reel_images],
            "reel_music": str(music_path.resolve()) if music_path else None,
            "work_dir": str(work.resolve()),
        },
    }

    for i, b in enumerate(bundles):
        dmeta = destinations[i] if i < len(destinations) else {}
        row: dict[str, Any] = {
            "destination": b.destination,
            "pexels_query_used": b.query,
            "scape_types": dmeta.get("scape_types"),
            "vibe": dmeta.get("vibe"),
            "groq_pexels_search_query": dmeta.get("pexels_search_query"),
            "image_urls": list(b.image_urls),
            "video_meta": b.video,
            "local_images": [str(p.resolve()) for p in image_paths_by_dest[i]],
            "local_video": None,
        }
        summary["media"].append(row)

    summary_path = work / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["outputs"]["summary_json"] = str(summary_path.resolve())

    return summary


def run_batch(themes: list[str]) -> list[dict[str, Any]]:
    """Run `run_pipeline` for each theme; failures are logged and skipped."""
    results: list[dict[str, Any]] = []
    for t in themes:
        t = t.strip()
        if not t or t.startswith("#"):
            continue
        try:
            results.append(run_pipeline(t))
        except Exception as e:
            logger.exception("Batch item failed for theme %r: %s", t, e)
            results.append({"theme": t, "error": str(e)})
    return results
