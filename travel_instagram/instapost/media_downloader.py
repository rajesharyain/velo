from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


async def download_to_file(url: str, dest_path: Path, client: httpx.AsyncClient) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with dest_path.open("wb") as f:
            async for chunk in resp.aiter_bytes():
                if chunk:
                    f.write(chunk)
    return dest_path


async def download_media_set(
    *,
    video_urls: list[str],
    image_urls: list[str],
    work_dir: Path,
) -> tuple[list[Path], list[Path]]:
    """
    Download selected Pexels URLs into work_dir.

    Returns: (local_video_paths, local_image_paths)
    """
    videos_dir = work_dir / "videos"
    images_dir = work_dir / "images"
    videos_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(120.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks: list[tuple[str, Path]] = []
        for i, u in enumerate(video_urls):
            ext = ".mp4"
            fname = f"clip_{i:02d}{ext}"
            tasks.append((u, videos_dir / fname))
        for j, u in enumerate(image_urls):
            ext = ".jpg"
            fname = f"img_{j:02d}{ext}"
            tasks.append((u, images_dir / fname))

        out_videos: list[Path] = []
        out_images: list[Path] = []

        sem = asyncio.Semaphore(6)

        async def _dl(u: str, p: Path) -> None:
            async with sem:
                try:
                    await download_to_file(u, p, client)
                except Exception as e:
                    logger.warning("Download failed (%s): %s", u[:80], e)

        await asyncio.gather(*[_dl(u, p) for (u, p) in tasks])

    # Collect what exists.
    for p in sorted(videos_dir.glob("clip_*.mp4")):
        if p.is_file() and p.stat().st_size > 1024 * 50:
            out_videos.append(p)
    for p in sorted(images_dir.glob("img_*.jpg")):
        if p.is_file() and p.stat().st_size > 1024 * 20:
            out_images.append(p)

    return out_videos, out_images

