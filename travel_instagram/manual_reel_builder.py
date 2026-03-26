from __future__ import annotations

import json
import random
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from travel_instagram import config
from travel_instagram import media_processor


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:40] or "manual"


def _is_video(p: Path) -> bool:
    return p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}


def _try_font(paths: list[str], size: int) -> ImageFont.ImageFont:
    for p in paths:
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_caption_overlay(out_png: Path, caption: str) -> Path:
    w, h = config.REEL_SIZE
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    text = (caption or "").strip()
    if not text:
        img.save(out_png)
        return out_png

    font = _try_font(
        [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"],
        int(h * 0.045),
    )
    max_w = int(w * 0.84)

    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for wtok in words:
        trial = " ".join(cur + [wtok]).strip()
        bb = draw.textbbox((0, 0), trial, font=font)
        if bb[2] - bb[0] <= max_w:
            cur.append(wtok)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [wtok]
    if cur:
        lines.append(" ".join(cur))
    lines = lines[:4]

    line_gap = max(8, int(h * 0.008))
    heights = [draw.textbbox((0, 0), ln, font=font)[3] - draw.textbbox((0, 0), ln, font=font)[1] for ln in lines]
    block_h = sum(heights) + max(0, len(lines) - 1) * line_gap
    block_w = 0
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        block_w = max(block_w, bb[2] - bb[0])

    pad_x = int(max(30, w * 0.06))
    pad_y = int(max(20, h * 0.022))
    rect_w = min(w - 36, block_w + pad_x * 2)
    rect_h = block_h + pad_y * 2
    x0 = (w - rect_w) // 2
    y0 = int(h * 0.68)
    y0 = max(10, min(y0, h - rect_h - 10))

    draw.rounded_rectangle(
        (x0, y0, x0 + rect_w, y0 + rect_h),
        radius=int(min(34, h * 0.03)),
        fill=(8, 12, 22, 208),
    )

    cy = y0 + pad_y
    cx = w // 2
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        tw = bb[2] - bb[0]
        tx = cx - tw // 2
        draw.text((tx + 3, cy + 3), ln, font=font, fill=(0, 0, 0, 170))
        draw.text((tx, cy), ln, font=font, fill=(245, 248, 252, 252))
        cy += (bb[3] - bb[1]) + line_gap

    img.save(out_png)
    return out_png


def _make_segment_from_image(src: Path, overlay_png: Path, out_mp4: Path, seconds: float) -> None:
    w, h = config.REEL_SIZE
    exe = media_processor._ensure_ffmpeg()  # type: ignore[attr-defined]
    cmd = [
        exe,
        "-y",
        "-loop",
        "1",
        "-i",
        str(src),
        "-i",
        str(overlay_png),
        "-filter_complex",
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=yuv420p,fps=30[v];[1:v]format=rgba[ov];[v][ov]overlay=0:0:format=auto",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-t",
        f"{seconds:.3f}",
        str(out_mp4),
    ]
    media_processor._run_ffmpeg_cmd(cmd, "manual reel image segment")  # type: ignore[attr-defined]


def _make_segment_from_video(src: Path, overlay_png: Path, out_mp4: Path, seconds: float) -> None:
    w, h = config.REEL_SIZE
    exe = media_processor._ensure_ffmpeg()  # type: ignore[attr-defined]
    cmd = [
        exe,
        "-y",
        "-i",
        str(src),
        "-i",
        str(overlay_png),
        "-filter_complex",
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=yuv420p,fps=30,trim=duration={seconds:.3f},setpts=PTS-STARTPTS[v];[1:v]format=rgba[ov];[v][ov]overlay=0:0:format=auto",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        str(out_mp4),
    ]
    media_processor._run_ffmpeg_cmd(cmd, "manual reel video segment")  # type: ignore[attr-defined]


def build_manual_reel(
    *,
    uploads_dir: Path,
    media_paths: list[Path],
    captions: list[str],
    music_track_id: str | None,
    transition_type: str = "auto",
    transition_speed: str = "auto",
) -> dict[str, Any]:
    if not media_paths:
        raise RuntimeError("Upload at least one image or video.")
    if len(captions) < len(media_paths):
        captions = captions + [""] * (len(media_paths) - len(captions))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_manual_" + _slug(media_paths[0].stem)
    out_dir = config.OUTPUT_DIR / "manual_reels" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    reel_work = out_dir / "work"
    reel_work.mkdir(parents=True, exist_ok=True)

    n = len(media_paths)
    fps = 30
    per = max(2.0, min(6.0, float(getattr(config, "REEL_SECONDS_PER_SLIDE", 2.9))))
    total = max(8.0, min(90.0, n * per))
    seg = max(1.0 / fps, int(round((total / n) * fps)) / float(fps))
    xfade = 0.0
    out_duration = seg * n

    # Transition controls are implemented at the concat stage.
    # For "none" we use hard cuts (concat filter) instead of xfade.
    none_mode = transition_type == "none"
    if n > 1 and not none_mode:
        base_xfade = media_processor._reel_pick_xfade_seconds(seg)  # type: ignore[attr-defined]
        speed = (transition_speed or "auto").lower().strip()
        speed_factor = 1.0
        if speed == "slow":
            speed_factor = 0.65
        elif speed == "slower":
            speed_factor = 0.55
        elif speed == "slowest":
            speed_factor = 0.48
        elif speed in {"fast", "faster"}:
            speed_factor = 1.35
        elif speed in {"default", "normal"}:
            speed_factor = 1.0
        elif speed in {"auto"}:
            speed_factor = 1.0
        else:
            raise RuntimeError(f"Invalid transition_speed={transition_speed!r}")

        xfade = base_xfade * speed_factor

        # Clamp to same safe bounds the picker uses (keeps "too fast" from breaking).
        lo = float(getattr(config, "REEL_XFADE_MIN_SECONDS", 0.42))
        hi = float(getattr(config, "REEL_XFADE_MAX_SECONDS", 0.95))
        xfade = max(lo, min(hi, xfade))

        # Avoid xfade durations that consume the segment too aggressively.
        if xfade >= seg - 0.06:
            xfade = max(lo * 0.82, min(hi * 0.88, seg * 0.38))

        out_duration = seg * n - max(0, n - 1) * xfade

    seg_paths: list[Path] = []
    for i, src in enumerate(media_paths):
        ov = reel_work / f"overlay_{i:02d}.png"
        _render_caption_overlay(ov, captions[i] if i < len(captions) else "")
        segp = reel_work / f"seg_{i:02d}.mp4"
        if _is_video(src):
            _make_segment_from_video(src, ov, segp, seg)
        else:
            _make_segment_from_image(src, ov, segp, seg)
        seg_paths.append(segp)

    no_audio = out_dir / "reel_noaudio.mp4"
    if n == 1:
        shutil.copy(seg_paths[0], no_audio)
    elif none_mode:
        # Hard cuts: concat the MP4 segments without transitions.
        exe = media_processor._ensure_ffmpeg()  # type: ignore[attr-defined]
        cmd: list[str] = [exe, "-y", "-fflags", "+genpts"]
        for p in seg_paths:
            cmd.extend(["-i", str(p)])
        v_inputs = "".join([f"[{i}:v]" for i in range(len(seg_paths))])
        n_seg = len(seg_paths)
        cmd.extend(
            [
                "-filter_complex",
                f"{v_inputs}concat=n={n_seg}:v=1:a=0[v]",
                "-map",
                "[v]",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-t",
                f"{out_duration:.3f}",
                str(no_audio),
            ]
        )
        media_processor._run_ffmpeg_cmd(cmd, "manual reel hard cuts concat")  # type: ignore[attr-defined]
    else:
        transition_style = None if transition_type == "auto" else transition_type
        media_processor._xfade_concat_reel_segments(  # type: ignore[attr-defined]
            seg_paths,
            seg,
            xfade,
            out_duration,
            no_audio,
            context="manual reel xfade concat",
            transition_style=transition_style,
        )

    out_mp4 = out_dir / "reel.mp4"
    music_path = config.resolve_reel_music(music_track_id)
    if music_path is not None and music_path.is_file():
        media_processor._mux_music(no_audio, music_path, out_mp4)  # type: ignore[attr-defined]
    else:
        shutil.copy(no_audio, out_mp4)

    return {
        "run_id": run_id,
        "output_path": str(out_mp4.resolve()),
        "media_count": n,
        "used_music": str(music_path.resolve()) if music_path else None,
        "segments": [
            {
                "path": str(p.resolve()),
                "type": "video" if _is_video(p) else "image",
                "caption": captions[i] if i < len(captions) else "",
            }
            for i, p in enumerate(media_paths)
        ],
    }

