from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from travel_instagram import config, groq_service, media_processor, pexels_service


def _slug(s: str, max_len: int = 48) -> str:
    import re

    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return (s[:max_len] or "theme").replace("-", "_")


def _safe_media_url(abs_path: Path) -> str:
    out = config.OUTPUT_DIR.resolve()
    resolved = abs_path.resolve()
    rel = resolved.relative_to(out)
    return "/media/" + rel.as_posix()


def autofill_media_for_theme(
    theme: str,
    *,
    max_items: int = 8,
    include_video: bool = True,
) -> dict[str, Any]:
    """
    Groq → Pexels → download media (images + optional portrait videos).

    Returns server-local asset ids that the UI can remove/replace before generating
    the final reel.
    """
    theme = (theme or "").strip()
    if not theme:
        raise RuntimeError("theme must be non-empty")

    config.ensure_output_dirs()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_autofill_" + _slug(theme)
    autofill_base = config.OUTPUT_DIR / "manual_reels" / "autofill"
    assets_dir = autofill_base / run_id / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    content = groq_service.generate_travel_content(theme)
    destinations = list(content.get("destinations") or [])
    if not destinations:
        raise RuntimeError("Groq returned no destinations.")

    # Download “enough” for editing; user can still remove and upload more.
    max_items = max(1, int(max_items))
    target_videos = min(3, max(0, max_items // 4))  # bias towards a few videos
    target_images = max(1, max_items - (target_videos if include_video else 0))

    used_image_urls: set[str] = set()
    images_downloaded = 0
    videos_downloaded = 0
    items: list[dict[str, Any]] = []

    # Download with one shared client for speed.
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for i, dest in enumerate(destinations):
            if len(items) >= max_items:
                break
            if not isinstance(dest, dict):
                continue

            dest_name = str(dest.get("destination") or "").strip() or "destination"
            pq = str(dest.get("pexels_search_query") or "").strip() or None
            caption = str(dest.get("caption") or "").strip() or dest_name

            bundle = pexels_service.fetch_media_for_destination(
                dest_name,
                include_video=include_video,
                pexels_search_query=pq,
                exclude_image_urls=used_image_urls,
            )

            # Images (download up to what we still need)
            for j, img_url in enumerate(bundle.image_urls or []):
                if len(items) >= max_items or images_downloaded >= target_images:
                    break
                url_l = str(img_url).lower()
                ext = ".png" if ".png" in url_l else ".jpg"
                out_path = assets_dir / f"img_{i:02d}_{j:02d}{ext}"
                try:
                    media_processor.download_binary(img_url, out_path, client=client)
                    used_image_urls.add(img_url)
                    images_downloaded += 1
                    items.append(
                        {
                            "asset_id": str(out_path.resolve().relative_to(autofill_base.resolve())),
                            "kind": "image",
                            "web_url": _safe_media_url(out_path),
                            "caption": caption,
                        }
                    )
                except Exception:
                    # Keep going; UI can still use remaining assets.
                    continue

            if len(items) >= max_items:
                break

            # Video (optional)
            if include_video and videos_downloaded < target_videos and bundle.video and len(items) < max_items:
                v = bundle.video
                v_url = v.get("url")
                if v_url:
                    out_path = assets_dir / f"vid_{i:02d}_00.mp4"
                    try:
                        media_processor.download_binary(str(v_url), out_path, client=client)
                        videos_downloaded += 1
                        items.append(
                            {
                                "asset_id": str(out_path.resolve().relative_to(autofill_base.resolve())),
                                "kind": "video",
                                "web_url": _safe_media_url(out_path),
                                "caption": caption,
                            }
                        )
                    except Exception:
                        pass

    if not items:
        raise RuntimeError("Pexels download failed: no images/videos available.")

    # Light shuffle but keep destination “grouping” roughly: avoid shuffling within same kind too much.
    # The UI supports drag ordering anyway; this just gives a decent default mix.
    if len(items) > 3:
        random.shuffle(items)

    return {"run_id": run_id, "items": items}

