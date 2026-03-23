from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from travel_instagram import config
from travel_instagram.instapost import ffmpeg_reel_builder
from travel_instagram.instapost import groq_script_service
from travel_instagram.instapost import media_downloader
from travel_instagram.instapost import pexels_service

logger = logging.getLogger(__name__)


def _abs_path_under_output(p: str | Path) -> Path | None:
    try:
        resolved = Path(p).resolve()
        out = config.OUTPUT_DIR.resolve()
        resolved.relative_to(out)
        return resolved
    except (ValueError, OSError):
        return None


def _to_media_url(abs_path: str | Path) -> str | None:
    p = _abs_path_under_output(abs_path)
    if p is None:
        return None
    rel = p.relative_to(config.OUTPUT_DIR.resolve())
    return "/media/" + rel.as_posix()


def _normalize_music_selection(music_track_id: str | None) -> str | None:
    if music_track_id is None:
        return None
    s = str(music_track_id).strip()
    if not s or s == "__auto__":
        return None
    return s


def _build_pexels_query(destination_query: str, visual: str | None) -> str:
    dest = destination_query.strip()
    if not dest:
        return "travel cinematic drone city"

    # Lightweight keyword extraction (keeps it deterministic and avoids odd tokens).
    allowed = {
        "drone",
        "aerial",
        "cinematic",
        "city",
        "driving",
        "road",
        "street",
        "night",
        "sunset",
        "beach",
        "mountain",
        "lake",
        "river",
        "temple",
        "market",
        "architecture",
        "travel",
        "adventure",
        "walk",
    }
    words = re.findall(r"[a-zA-Z]+", str(visual or "").lower())
    extra = [w for w in words if w in allowed]
    extras = " ".join(dict.fromkeys(extra))  # preserve order, unique

    fixed = "travel cinematic drone city"
    if extras:
        return f"{dest} {extras} {fixed}"
    return f"{dest} {fixed}"


async def generate_instapost(
    *,
    destination_query: str,
    variations: int = 1,
    music_track_id: str | None = None,
) -> dict[str, Any]:
    """
    Generate one or multiple InstaPost reels.

    This function is async and handles Groq (in thread), Pexels (async),
    downloads (async), and FFmpeg (in thread).
    """
    config.ensure_output_dirs()
    dest = destination_query.strip()
    if not dest:
        raise ValueError("destination_query must be non-empty.")

    variations = int(variations)
    if variations < 1:
        variations = 1
    if variations > 5:
        variations = 5

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_instapost_{abs(hash(dest)) % 10000:04d}"
    base_dir = (config.OUTPUT_DIR / "instapost" / run_id).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    reel_dir = base_dir / "reels"
    reel_dir.mkdir(parents=True, exist_ok=True)
    work_media_dir = base_dir / "media"
    work_media_dir.mkdir(parents=True, exist_ok=True)

    logger.info("InstaPost: generating scripts for %r (variations=%s)", dest, variations)
    scripts = await asyncio.to_thread(groq_script_service.generate_scripts, dest, variations)
    if not scripts:
        raise RuntimeError("Groq returned no scripts.")

    pexels_query = _build_pexels_query(dest, scripts[0].get("visual") or None)
    video_count = 4
    image_count = 3

    logger.info("InstaPost: fetching Pexels media query=%r", pexels_query)
    bundle = await pexels_service.fetch_insta_media(
        dest,
        video_count=video_count,
        image_count=image_count,
        pexels_search_query=pexels_query,
    )

    if not bundle.videos and not bundle.images:
        raise RuntimeError("No Pexels media found for this destination. Try a different query.")

    video_urls = [v.url for v in bundle.videos]
    image_urls = list(bundle.images)

    local_video_paths, local_image_paths = await media_downloader.download_media_set(
        video_urls=video_urls,
        image_urls=image_urls,
        work_dir=work_media_dir,
    )

    clip_paths: list[Path] = []
    clip_paths.extend(local_video_paths)
    clip_paths.extend(local_image_paths)

    if not clip_paths:
        raise RuntimeError("Downloaded media set was empty. Please try again.")

    music_path = config.resolve_reel_music(_normalize_music_selection(music_track_id))

    # Save captions/scripts now.
    scripts_path = base_dir / "scripts.json"
    scripts_path.write_text(json.dumps(scripts, ensure_ascii=False, indent=2), encoding="utf-8")

    reels: list[dict[str, Any]] = []
    for i, sc in enumerate(scripts):
        v_work = reel_dir / f"variation_{i+1:02d}"
        v_work.mkdir(parents=True, exist_ok=True)

        reel_mp4 = await asyncio.to_thread(
            ffmpeg_reel_builder.build_instapost_reel,
            work_dir=v_work,
            clip_paths=clip_paths,
            hook=sc.get("hook") or "",
            value=sc.get("value") or "",
            cta=sc.get("cta") or "",
            music_path=music_path,
            total_duration_seconds=None,
        )
        reel_url = _to_media_url(reel_mp4)
        captions_url = _to_media_url(scripts_path)
        reels.append(
            {
                "variation_index": i,
                "reel_mp4_path": str(reel_mp4),
                "reel_url": reel_url,
                "script": sc,
                "captions_json_url": captions_url,
            }
        )

    out = {
        "run_id": run_id,
        "destination_query": dest,
        "generated_variations": len(reels),
        "reels": reels,
        "scripts_json_url": _to_media_url(scripts_path),
    }
    return out

