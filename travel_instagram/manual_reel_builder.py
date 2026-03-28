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

# Caption backdrop on manual upload-reel overlays. None = text only (strokes still help readability).
# Restore the previous pill: set to (8, 12, 22, 208) — dark blue-black at ~81% alpha.
CAPTION_OVERLAY_PANEL_RGBA: tuple[int, int, int, int] | None = None


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


def infer_overlay_title_from_caption(caption: str) -> str:
    """If the caption starts with a location clause before '. ', use that as the overlay title."""
    cap = (caption or "").strip()
    if not cap or ". " not in cap:
        return ""
    head, _tail = cap.split(". ", 1)
    head = head.strip()
    if 4 <= len(head) <= 88 and ("," in head or len(head.split()) <= 10):
        return head
    return ""


def strip_leading_title_from_caption(caption: str, title: str) -> str:
    """Remove a leading location/title from caption text so the overlay body does not repeat it."""
    cap = (caption or "").strip()
    tit = (title or "").strip()
    if not tit or not cap:
        return cap
    low_c, low_t = cap.lower(), tit.lower()
    if low_c.startswith(low_t):
        rest = cap[len(tit) :].lstrip(" .—:，")
        return rest if rest.strip() else cap
    pref = tit + "."
    if low_c.startswith(pref.lower()):
        rest = cap[len(pref) :].strip()
        return rest if rest else cap
    return cap


def _wrap_words_to_lines(
    draw: Any,
    text: str,
    font: ImageFont.ImageFont,
    max_w: int,
    max_lines: int,
) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
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
                if len(lines) >= max_lines:
                    break
                cur = []
            one = draw.textbbox((0, 0), wtok, font=font)
            if one[2] - one[0] > max_w:
                lines.append(wtok)
                if len(lines) >= max_lines:
                    break
                cur = []
            else:
                cur = [wtok]
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    return lines[:max_lines]


def _render_caption_overlay(
    out_png: Path,
    caption: str,
    *,
    title: str = "",
    anchor_x: float = 0.5,
    anchor_y: float = 0.5,
    font_scale: float = 1.0,
) -> Path:
    w, h = config.REEL_SIZE
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    title_t = (title or "").strip()
    body_t = (caption or "").strip()

    anchor_x = max(0.0, min(1.0, float(anchor_x)))
    anchor_y = max(0.0, min(1.0, float(anchor_y)))
    font_scale = max(0.6, min(1.7, float(font_scale)))

    max_w = int(w * 0.84)
    line_gap = max(8, int(h * 0.008))
    title_body_gap = max(10, int(h * 0.014))
    pad_x = int(max(30, w * 0.06))
    pad_y = int(max(20, h * 0.022))

    if not title_t and not body_t:
        img.save(out_png)
        return out_png

    # Single block: legacy one-style caption (no separate title)
    if not title_t:
        font = _try_font(
            [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"],
            int(h * 0.045 * font_scale),
        )
        lines = _wrap_words_to_lines(draw, body_t, font, max_w, 4)
        if not lines:
            img.save(out_png)
            return out_png
        heights = [
            draw.textbbox((0, 0), ln, font=font)[3] - draw.textbbox((0, 0), ln, font=font)[1]
            for ln in lines
        ]
        block_h = sum(heights) + max(0, len(lines) - 1) * line_gap
        block_w = max(
            draw.textbbox((0, 0), ln, font=font)[2] - draw.textbbox((0, 0), ln, font=font)[0]
            for ln in lines
        )
        rect_w = min(w - 36, block_w + pad_x * 2)
        rect_h = block_h + pad_y * 2
        cx = float(anchor_x) * float(w)
        cy = float(anchor_y) * float(h)
        x0 = int(round(cx - rect_w / 2.0))
        y0 = int(round(cy - rect_h / 2.0))
        x0 = max(10, min(x0, w - rect_w - 10))
        y0 = max(10, min(y0, h - rect_h - 10))
        if CAPTION_OVERLAY_PANEL_RGBA is not None:
            draw.rounded_rectangle(
                (x0, y0, x0 + rect_w, y0 + rect_h),
                radius=int(min(34, h * 0.03)),
                fill=CAPTION_OVERLAY_PANEL_RGBA,
            )
        cy_line = y0 + pad_y
        stroke_w = max(1, int(round(font_scale * 1.1)))
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=font)
            tw = bb[2] - bb[0]
            tx = int(round((x0 + rect_w / 2.0) - tw / 2.0))
            draw.text(
                (tx, cy_line),
                ln,
                font=font,
                fill=(245, 248, 252, 252),
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 160),
            )
            cy_line += (bb[3] - bb[1]) + line_gap
        img.save(out_png)
        return out_png

    title_font = _try_font(
        [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"],
        int(h * 0.052 * font_scale),
    )
    body_font = _try_font(
        [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"],
        int(h * 0.038 * font_scale),
    )
    title_lines = _wrap_words_to_lines(draw, title_t, title_font, max_w, 2)
    body_lines = _wrap_words_to_lines(draw, body_t, body_font, max_w, 5) if body_t else []

    th = [
        draw.textbbox((0, 0), ln, font=title_font)[3]
        - draw.textbbox((0, 0), ln, font=title_font)[1]
        for ln in title_lines
    ]
    bh = [
        draw.textbbox((0, 0), ln, font=body_font)[3] - draw.textbbox((0, 0), ln, font=body_font)[1]
        for ln in body_lines
    ]
    block_h = sum(th) + max(0, len(title_lines) - 1) * line_gap
    if body_lines:
        block_h += title_body_gap + sum(bh) + max(0, len(body_lines) - 1) * line_gap

    block_w = 0
    for ln in title_lines:
        bb = draw.textbbox((0, 0), ln, font=title_font)
        block_w = max(block_w, bb[2] - bb[0])
    for ln in body_lines:
        bb = draw.textbbox((0, 0), ln, font=body_font)
        block_w = max(block_w, bb[2] - bb[0])

    rect_w = min(w - 36, block_w + pad_x * 2)
    rect_h = block_h + pad_y * 2
    cx = float(anchor_x) * float(w)
    cy = float(anchor_y) * float(h)
    x0 = int(round(cx - rect_w / 2.0))
    y0 = int(round(cy - rect_h / 2.0))
    x0 = max(10, min(x0, w - rect_w - 10))
    y0 = max(10, min(y0, h - rect_h - 10))

    if CAPTION_OVERLAY_PANEL_RGBA is not None:
        draw.rounded_rectangle(
            (x0, y0, x0 + rect_w, y0 + rect_h),
            radius=int(min(34, h * 0.03)),
            fill=CAPTION_OVERLAY_PANEL_RGBA,
        )

    cy_line = y0 + pad_y
    title_stroke = max(1, int(round(font_scale * 1.25)))
    body_stroke = max(1, int(round(font_scale * 0.95)))
    for ln in title_lines:
        bb = draw.textbbox((0, 0), ln, font=title_font)
        tw = bb[2] - bb[0]
        tx = int(round((x0 + rect_w / 2.0) - tw / 2.0))
        draw.text(
            (tx, cy_line),
            ln,
            font=title_font,
            fill=(255, 255, 255, 255),
            stroke_width=title_stroke,
            stroke_fill=(0, 0, 0, 170),
        )
        cy_line += (bb[3] - bb[1]) + line_gap

    if body_lines:
        cy_line += title_body_gap - line_gap

    for ln in body_lines:
        bb = draw.textbbox((0, 0), ln, font=body_font)
        tw = bb[2] - bb[0]
        tx = int(round((x0 + rect_w / 2.0) - tw / 2.0))
        draw.text(
            (tx, cy_line),
            ln,
            font=body_font,
            fill=(230, 235, 245, 252),
            stroke_width=body_stroke,
            stroke_fill=(0, 0, 0, 150),
        )
        cy_line += (bb[3] - bb[1]) + line_gap

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
    overlay_positions: list[tuple[float, float]] | None = None,
    overlay_font_scales: list[float] | None = None,
    titles: list[str] | None = None,
) -> dict[str, Any]:
    if not media_paths:
        raise RuntimeError("Upload at least one image or video.")
    if len(captions) < len(media_paths):
        captions = captions + [""] * (len(media_paths) - len(captions))
    tit_list = list(titles) if titles else []
    if len(tit_list) < len(media_paths):
        tit_list = tit_list + [""] * (len(media_paths) - len(tit_list))

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

    if overlay_positions is None:
        overlay_positions = [(0.5, 0.5)] * n
    if overlay_font_scales is None:
        overlay_font_scales = [1.0] * n
    if len(overlay_positions) < n:
        overlay_positions = list(overlay_positions) + [(0.5, 0.72)] * (n - len(overlay_positions))
    if len(overlay_font_scales) < n:
        overlay_font_scales = list(overlay_font_scales) + [1.0] * (n - len(overlay_font_scales))

    for i, src in enumerate(media_paths):
        ov = reel_work / f"overlay_{i:02d}.png"
        anchor = overlay_positions[i] if i < len(overlay_positions) else (0.5, 0.72)
        fs = overlay_font_scales[i] if i < len(overlay_font_scales) else 1.0
        cap_i = captions[i] if i < len(captions) else ""
        tit_i = (tit_list[i] if i < len(tit_list) else "").strip()
        cap_clean = (cap_i or "").strip()
        if not tit_i and cap_clean:
            tit_i = infer_overlay_title_from_caption(cap_clean)
            if tit_i:
                cap_clean = strip_leading_title_from_caption(cap_clean, tit_i).strip()
        elif tit_i and cap_clean:
            stripped = strip_leading_title_from_caption(cap_clean, tit_i).strip()
            if stripped:
                cap_clean = stripped
        _render_caption_overlay(
            ov,
            cap_clean,
            title=tit_i,
            anchor_x=anchor[0],
            anchor_y=anchor[1],
            font_scale=fs,
        )
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

