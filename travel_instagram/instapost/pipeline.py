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


def _clip_line_for_source(place: str, kind: str, idx: int) -> str:
    p = place.strip() or "this destination"
    if kind == "video":
        variants = [
            f"Scenic views from {p}",
            f"A travel moment in {p}",
            f"Moving through {p}",
            f"Discovering {p} in motion",
        ]
    else:
        variants = [
            f"A snapshot from {p}",
            f"Postcard view of {p}",
            f"A still moment in {p}",
            f"Beautiful frame from {p}",
        ]
    return variants[idx % len(variants)]


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
    vibe_pack = await asyncio.to_thread(groq_script_service.generate_destination_vibes, dest)
    vibe_places = list(vibe_pack.get("places") or [])
    vibe_lines = list(vibe_pack.get("vibe_lines") or [])
    places_for_media = [p for p in vibe_places if str(p).strip()]
    if not places_for_media:
        places_for_media = [dest]
    # Keep total request size practical while still covering multiple places.
    places_for_media = places_for_media[:5]

    visual_hint = scripts[0].get("visual") or None
    video_count_each = 3
    image_count_each = 2

    fetch_tasks = []
    for place in places_for_media:
        q = _build_pexels_query(str(place), visual_hint)
        logger.info("InstaPost: fetching Pexels media for place=%r query=%r", place, q)
        fetch_tasks.append(
            pexels_service.fetch_insta_media(
                str(place),
                video_count=video_count_each,
                image_count=image_count_each,
                pexels_search_query=q,
            )
        )
    bundles = await asyncio.gather(*fetch_tasks)

    # Merge + de-duplicate URLs, preserving first-seen order by place.
    video_urls: list[str] = []
    image_urls: list[str] = []
    url_meta: dict[str, tuple[str, str]] = {}  # url -> (place, kind)
    seen_v: set[str] = set()
    seen_i: set[str] = set()
    for b in bundles:
        for v in b.videos:
            if v.url not in seen_v:
                seen_v.add(v.url)
                video_urls.append(v.url)
                url_meta[v.url] = (str(b.destination or dest), "video")
        for u in b.images:
            if u not in seen_i:
                seen_i.add(u)
                image_urls.append(u)
                url_meta[u] = (str(b.destination or dest), "image")

    # Fallback to a single broad query if all place-specific searches miss.
    if not video_urls and not image_urls:
        fallback_q = _build_pexels_query(dest, visual_hint)
        logger.info("InstaPost: place-specific media empty, fallback query=%r", fallback_q)
        fallback = await pexels_service.fetch_insta_media(
            dest,
            video_count=4,
            image_count=3,
            pexels_search_query=fallback_q,
        )
        video_urls = [v.url for v in fallback.videos]
        image_urls = list(fallback.images)
        url_meta = {}
        for v in fallback.videos:
            url_meta[v.url] = (str(fallback.destination or dest), "video")
        for u in fallback.images:
            url_meta[u] = (str(fallback.destination or dest), "image")

    if not video_urls and not image_urls:
        raise RuntimeError("No Pexels media found for the requested destinations. Try a different query.")

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

    # Clip-aligned description lines: map each downloaded local clip to the place
    # that provided its source URL, so captions stay consistent with media origin.
    per_clip_lines: list[str] = []
    for i, _ in enumerate(local_video_paths):
        url = video_urls[i] if i < len(video_urls) else ""
        place, kind = url_meta.get(url, (dest, "video"))
        per_clip_lines.append(_clip_line_for_source(place, kind, i))
    for j, _ in enumerate(local_image_paths):
        url = image_urls[j] if j < len(image_urls) else ""
        place, kind = url_meta.get(url, (dest, "image"))
        per_clip_lines.append(_clip_line_for_source(place, kind, j))

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
            title=sc.get("title") or sc.get("hook") or "",
            caption=sc.get("caption") or sc.get("value") or "",
            place_text=f"For visiting {dest} cheap visit budgetwing.com",
            per_clip_vibes=per_clip_lines,
            cta="Save more at budgetwing.com",
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
                "vibe_places": vibe_places,
                "vibe_lines": vibe_lines,
                "captions_json_url": captions_url,
            }
        )

    out = {
        "run_id": run_id,
        "destination_query": dest,
        "media_places_used": places_for_media,
        "generated_variations": len(reels),
        "reels": reels,
        "scripts_json_url": _to_media_url(scripts_path),
    }
    return out

