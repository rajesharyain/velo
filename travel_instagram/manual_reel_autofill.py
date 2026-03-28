from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from travel_instagram import config, groq_service, media_processor, pexels_service
from travel_instagram.manual_reel_builder import strip_leading_title_from_caption


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

    If the theme includes a count (e.g. "top 10 places in Portugal"), Groq returns
    that many destinations and we download roughly one asset per row (image or video).

    Returns server-local asset ids that the UI can remove/replace before generating
    the final reel.
    """
    theme = (theme or "").strip()
    if not theme:
        raise RuntimeError("theme must be non-empty")

    config.ensure_output_dirs()

    explicit_n = groq_service.infer_requested_destination_count(theme)
    max_items = min(20, max(1, max_items, explicit_n or 0))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_autofill_" + _slug(theme)
    autofill_base = config.OUTPUT_DIR / "manual_reels" / "autofill"
    assets_dir = autofill_base / run_id / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    content = groq_service.generate_travel_content(theme, destination_count=explicit_n)
    destinations = list(content.get("destinations") or [])
    if not destinations:
        raise RuntimeError("Groq returned no destinations.")

    one_per_row = explicit_n is not None
    max_items = min(20, max(max_items, len(destinations)))

    target_videos = min(3, max(0, max_items // 4))
    target_images = max(1, max_items - (target_videos if include_video else 0))

    used_image_urls: set[str] = set()
    images_downloaded = 0
    videos_downloaded = 0
    items: list[dict[str, Any]] = []

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for i, dest in enumerate(destinations):
            if len(items) >= max_items:
                break
            if not isinstance(dest, dict):
                continue

            dest_name = str(dest.get("destination") or "").strip() or "destination"
            pq = str(dest.get("pexels_search_query") or "").strip() or None
            full_caption = str(dest.get("caption") or "").strip() or dest_name
            title = groq_service.base_location_label(dest_name) or dest_name
            caption_body = strip_leading_title_from_caption(full_caption, title).strip()
            if not caption_body:
                caption_body = full_caption

            bundle = pexels_service.fetch_media_for_destination(
                dest_name,
                include_video=include_video,
                pexels_search_query=pq,
                exclude_image_urls=used_image_urls,
            )

            if one_per_row:
                try_video = (
                    include_video
                    and videos_downloaded < max(1, min(4, max(1, len(destinations) // 3)))
                    and (i % 4 == 3)
                    and bundle.video
                    and bundle.video.get("url")
                )
                got_row = False
                if try_video:
                    v_url = bundle.video.get("url")
                    out_path = assets_dir / f"vid_{i:02d}_00.mp4"
                    try:
                        media_processor.download_binary(str(v_url), out_path, client=client)
                        videos_downloaded += 1
                        items.append(
                            {
                                "asset_id": str(out_path.resolve().relative_to(autofill_base.resolve())),
                                "kind": "video",
                                "web_url": _safe_media_url(out_path),
                                "destination": dest_name,
                                "title": title,
                                "caption": caption_body,
                            }
                        )
                        got_row = True
                    except Exception:
                        pass

                if not got_row:
                    for j, img_url in enumerate(bundle.image_urls or []):
                        if len(items) >= max_items:
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
                                    "asset_id": str(
                                        out_path.resolve().relative_to(autofill_base.resolve())
                                    ),
                                    "kind": "image",
                                    "web_url": _safe_media_url(out_path),
                                    "destination": dest_name,
                                    "title": title,
                                    "caption": caption_body,
                                }
                            )
                            break
                        except Exception:
                            continue
                continue

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
                            "destination": dest_name,
                            "title": title,
                            "caption": caption_body,
                        }
                    )
                except Exception:
                    continue

            if len(items) >= max_items:
                break

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
                                "destination": dest_name,
                                "title": title,
                                "caption": caption_body,
                            }
                        )
                    except Exception:
                        pass

    if not items:
        raise RuntimeError("Pexels download failed: no images/videos available.")

    # Keep Groq ranking order for explicit "top N" lists; shuffle only generic themes.
    if len(items) > 3 and not one_per_row:
        random.shuffle(items)

    out: dict[str, Any] = {"run_id": run_id, "items": items}
    if explicit_n is not None:
        out["requested_destination_count"] = explicit_n
    gf = content.get("theme_geo_focus")
    if isinstance(gf, str) and gf.strip():
        out["theme_geo_focus"] = gf.strip()
    return out
