"""
Discover generated reels from ``output/carousel/*/summary.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from travel_instagram import config

logger = logging.getLogger(__name__)


def _format_hashtags(tags: Any) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        return tags.strip()
    if isinstance(tags, list):
        return " ".join("#" + str(t).lstrip("#") for t in tags if str(t).strip())
    return ""


def _reel_public_path(reel_path: Path | None) -> str | None:
    if not reel_path or not reel_path.is_file():
        return None
    try:
        rel = reel_path.resolve().relative_to(config.OUTPUT_DIR.resolve())
        return "/media/" + rel.as_posix()
    except ValueError:
        return None


def list_reels() -> list[dict[str, Any]]:
    """
    All runs that have ``summary.json``, newest first.

    Each item includes ``title`` (content hook), ``hashtags`` (single string with #),
    ``reel_video_url`` when the MP4 still exists under ``OUTPUT_DIR``.
    """
    carousel = config.CAROUSEL_DIR
    if not carousel.is_dir():
        return []

    rows: list[dict[str, Any]] = []
    for d in sorted(carousel.iterdir()):
        if not d.is_dir():
            continue
        sj = d / "summary.json"
        if not sj.is_file():
            continue
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skip bad summary %s: %s", sj, e)
            continue

        content = data.get("content") or {}
        hook = (content.get("hook") or "").strip()
        hashtags_raw = content.get("hashtags")
        theme = (data.get("theme") or "").strip()
        run_id = (data.get("run_id") or d.name).strip()
        generated_at = (data.get("generated_at") or "").strip()
        outputs = data.get("outputs") or {}
        reel_path_str = outputs.get("reel_video") or ""
        reel_file = Path(reel_path_str) if reel_path_str else None
        reel_exists = bool(reel_file and reel_file.is_file())

        try:
            summary_rel = sj.resolve().relative_to(config.OUTPUT_DIR.resolve())
            summary_json_url = "/media/" + summary_rel.as_posix()
        except ValueError:
            summary_json_url = None

        hashtags_list: list[str] = []
        if isinstance(hashtags_raw, list):
            hashtags_list = [str(x) for x in hashtags_raw]
        elif isinstance(hashtags_raw, str) and hashtags_raw.strip():
            hashtags_list = [hashtags_raw.strip()]

        rows.append(
            {
                "run_id": run_id,
                "theme": theme,
                "title": hook,
                "hashtags": _format_hashtags(hashtags_raw),
                "hashtags_list": hashtags_list,
                "generated_at": generated_at,
                "reel_video_url": _reel_public_path(reel_file),
                "summary_json_url": summary_json_url,
                "reel_exists": reel_exists,
                "reel_filename": reel_file.name if reel_file else "",
            },
        )

    rows.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
    return rows
