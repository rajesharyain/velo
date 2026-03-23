"""
Export Instagram feed post dimensions from existing carousel slides (9:16 JPEGs).

Each output canvas is exactly the Instagram pixel size (e.g. 1080×1080). The slide
is **uniformly scaled** to fit entirely inside that canvas (letterbox/pillarbox),
then centered on a solid fill — no stretching and no cropping.

Writes derivatives under ``<run_dir>/instagram_feed/{square,portrait,landscape}/``
without modifying originals.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from travel_instagram import config

logger = logging.getLogger(__name__)

# Instagram recommended feed sizes
IG_SQUARE = (1080, 1080)  # 1:1
IG_PORTRAIT = (1080, 1350)  # 4:5
IG_LANDSCAPE = (1080, 566)  # ~1.91:1

# Bumped when fit algorithm changes so cached JPEGs are rebuilt.
INSTAGRAM_EXPORT_FIT_MODE = "contain_v1"
PAD_RGB = (20, 20, 24)


def safe_carousel_run_dir(run_id: str) -> Path:
    """Resolve ``run_id`` to a directory under ``CAROUSEL_DIR`` (no traversal)."""
    rid = (run_id or "").strip()
    if not rid or ".." in rid or "/" in rid or "\\" in rid:
        raise ValueError("Invalid run_id")
    p = (config.CAROUSEL_DIR / rid).resolve()
    base = config.CAROUSEL_DIR.resolve()
    try:
        p.relative_to(base)
    except ValueError as e:
        raise ValueError("Invalid run_id") from e
    return p


def _contain_pad_to(
    im: Image.Image,
    size: tuple[int, int],
    fill: tuple[int, int, int] = PAD_RGB,
) -> Image.Image:
    """Scale uniformly to fit inside ``size``, center on ``fill`` (no crop, no stretch)."""
    tw, th = size
    w, h = im.size
    if w < 1 or h < 1:
        return Image.new("RGB", size, fill)
    scale = min(tw / w, th / h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = im.resize((nw, nh), Image.Resampling.LANCZOS).convert("RGB")
    canvas = Image.new("RGB", size, fill)
    x = (tw - nw) // 2
    y = (th - nh) // 2
    canvas.paste(resized, (x, y))
    return canvas


def _to_media_url(abs_path: Path) -> str | None:
    try:
        resolved = abs_path.resolve()
        out = config.OUTPUT_DIR.resolve()
        rel = resolved.relative_to(out)
        return "/media/" + rel.as_posix()
    except (ValueError, OSError):
        return None


def _slide_jobs_from_summary(data: dict[str, Any]) -> list[tuple[int, Path]]:
    """(1-based slide index as in ``slide_XX.jpg``, source path) for files that exist."""
    outputs = data.get("outputs") or {}
    slides = outputs.get("carousel_slides") or []
    if not isinstance(slides, list):
        return []
    jobs: list[tuple[int, Path]] = []
    for idx, slide_str in enumerate(slides):
        src = Path(str(slide_str))
        if src.is_file():
            jobs.append((idx + 1, src))
    return jobs


def _urls_from_cached_export(run_dir: Path, jobs: list[tuple[int, Path]]) -> dict[str, Any] | None:
    marker = run_dir / "instagram_feed" / ".velo_instagram_fit"
    try:
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != INSTAGRAM_EXPORT_FIT_MODE:
            return None
    except OSError:
        return None

    out_root = run_dir / "instagram_feed"
    dirs = {
        "square": out_root / "square",
        "portrait": out_root / "portrait",
        "landscape": out_root / "landscape",
    }
    urls: dict[str, list[str]] = {"square": [], "portrait": [], "landscape": []}
    for ord_num, _ in jobs:
        stem = f"slide_{ord_num:02d}.jpg"
        batch: dict[str, str] = {}
        for key in ("square", "portrait", "landscape"):
            p = dirs[key] / stem
            if not p.is_file():
                return None
            u = _to_media_url(p)
            if not u:
                return None
            batch[key] = u
        for key in ("square", "portrait", "landscape"):
            urls[key].append(batch[key])
    return {
        "square": urls["square"],
        "portrait": urls["portrait"],
        "landscape": urls["landscape"],
        "slide_count": len(jobs),
    }


def _export_slide_jobs(run_dir: Path, jobs: list[tuple[int, Path]]) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    out_root = run_dir / "instagram_feed"
    dirs = {
        "square": out_root / "square",
        "portrait": out_root / "portrait",
        "landscape": out_root / "landscape",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    sizes = {
        "square": IG_SQUARE,
        "portrait": IG_PORTRAIT,
        "landscape": IG_LANDSCAPE,
    }

    urls: dict[str, list[str]] = {"square": [], "portrait": [], "landscape": []}

    for ord_num, src in jobs:
        stem = f"slide_{ord_num:02d}.jpg"
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                batch: dict[str, str] = {}
                for key, wh in sizes.items():
                    framed = _contain_pad_to(im, wh)
                    dest = dirs[key] / stem
                    framed.save(dest, "JPEG", quality=92, optimize=False, subsampling=2)
                    u = _to_media_url(dest)
                    if not u:
                        raise RuntimeError(f"Could not build media URL for {dest}")
                    batch[key] = u
                for key in ("square", "portrait", "landscape"):
                    urls[key].append(batch[key])
        except OSError as e:
            logger.warning("Could not process slide %s: %s", src, e)
            continue

    n = len(urls["square"])
    if n > 0:
        try:
            (out_root / ".velo_instagram_fit").write_text(
                INSTAGRAM_EXPORT_FIT_MODE + "\n",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Could not write instagram fit marker: %s", e)

    return {
        "square": urls["square"],
        "portrait": urls["portrait"],
        "landscape": urls["landscape"],
        "slide_count": n,
    }


def export_instagram_variants_for_run(run_dir: Path) -> dict[str, Any]:
    """
    Read ``summary.json`` in ``run_dir``, export each existing carousel slide to three sizes.

    Returns dict with keys ``square``, ``portrait``, ``landscape`` (lists of web URLs),
    and ``slide_count``.
    """
    run_dir = run_dir.resolve()
    base = config.CAROUSEL_DIR.resolve()
    try:
        run_dir.relative_to(base)
    except ValueError as e:
        raise ValueError("Invalid carousel run directory") from e

    sj = run_dir / "summary.json"
    if not sj.is_file():
        raise FileNotFoundError(f"No summary.json in {run_dir}")

    data = json.loads(sj.read_text(encoding="utf-8"))
    jobs = _slide_jobs_from_summary(data)
    if not jobs:
        return {"square": [], "portrait": [], "landscape": [], "slide_count": 0}

    return _export_slide_jobs(run_dir, jobs)


def get_or_build_instagram_feed(run_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Return feed URLs, reusing files on disk unless ``force`` is True."""
    run_dir = run_dir.resolve()
    base = config.CAROUSEL_DIR.resolve()
    try:
        run_dir.relative_to(base)
    except ValueError as e:
        raise ValueError("Invalid carousel run directory") from e

    sj = run_dir / "summary.json"
    if not sj.is_file():
        raise FileNotFoundError(f"No summary.json in {run_dir}")

    data = json.loads(sj.read_text(encoding="utf-8"))
    jobs = _slide_jobs_from_summary(data)
    if not jobs:
        return {"square": [], "portrait": [], "landscape": [], "slide_count": 0}

    if not force:
        cached = _urls_from_cached_export(run_dir, jobs)
        if cached is not None:
            return cached

    return _export_slide_jobs(run_dir, jobs)
