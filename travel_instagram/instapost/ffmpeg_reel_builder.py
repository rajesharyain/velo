from __future__ import annotations

import logging
import random
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from travel_instagram import config

logger = logging.getLogger(__name__)


_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _resolve_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in _FONT_CANDIDATES:
        fp = Path(p)
        if fp.is_file():
            try:
                return ImageFont.truetype(str(fp), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> tuple[int, int]:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> list[str]:
    words = str(text or "").strip().split()
    if not words:
        return []
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = (" ".join(cur + [w])).strip()
        tw, _ = _text_bbox(draw, trial, font)
        if tw <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _fit_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_size_start: int,
    max_width: int,
    max_lines: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    size = font_size_start
    for _ in range(8):
        font = _resolve_font(size)
        lines = _wrap_text(draw, text, font, max_width)
        lines = [ln for ln in lines if ln.strip()]
        if len(lines) <= max_lines:
            return font, lines
        size -= 2
        if size < 14:
            break
    font = _resolve_font(max(14, font_size_start - 16))
    lines = _wrap_text(draw, text, font, max_width)[:max_lines]
    return font, lines


def _draw_line_centered_budgetwing(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    x_center: int,
    y: int,
    default_fill: tuple[int, int, int, int],
    brand_fill: tuple[int, int, int, int],
) -> None:
    parts = re.split(r"(budgetwing\.com)", text, flags=re.IGNORECASE)
    valid = [p for p in parts if p]
    if not valid:
        return
    widths: list[int] = []
    for p in valid:
        w, _ = _text_bbox(draw, p, font)
        widths.append(w)
    total_w = sum(widths)
    x = int(x_center - total_w / 2)
    for p, pw in zip(valid, widths):
        is_brand = p.lower() == "budgetwing.com"
        fill = brand_fill if is_brand else default_fill
        draw.text((x + 4, y + 4), p, font=font, fill=(0, 0, 0, 210))
        draw.text((x, y), p, font=font, fill=fill)
        x += pw


def _render_overlay_full_frame(
    out_png: Path,
    *,
    text: str,
    frame_size: tuple[int, int],
    y: int,
    font_size: int,
    max_width_ratio: float,
    max_lines: int,
    bg_alpha: int = 150,
    highlight_budgetwing: bool = False,
) -> Path:
    w, h = frame_size
    out_png.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    max_width = int(w * max_width_ratio)

    font, lines = _fit_lines(draw, text, font_size, max_width=max_width, max_lines=max_lines)
    if not lines:
        img.save(out_png)
        return out_png

    # Measure block.
    line_heights: list[int] = []
    line_widths: list[int] = []
    for ln in lines:
        tw, th = _text_bbox(draw, ln, font)
        line_widths.append(tw)
        line_heights.append(th)
    block_w = max(line_widths)
    block_h = sum(line_heights) + (len(lines) - 1) * int(max(6, h * 0.006))

    x0 = (w - block_w) // 2
    y0 = max(0, min(h - block_h, y))

    pad_x = int(max(14, w * 0.02))
    pad_y = int(max(10, h * 0.015))
    rect = (x0 - pad_x, y0 - pad_y, x0 + block_w + pad_x, y0 + block_h + pad_y)

    # Semi-transparent contrast box.
    draw.rounded_rectangle(rect, radius=int(min(28, h * 0.03)), fill=(0, 0, 0, bg_alpha))

    # Center text block inside the rounded background.
    rect_w = rect[2] - rect[0]
    rect_h = rect[3] - rect[1]
    inner_h = block_h
    cur_y = rect[1] + max(0, (rect_h - inner_h) // 2)
    shadow_off = (4, 4)

    for idx, ln in enumerate(lines):
        tw, th = _text_bbox(draw, ln, font)
        x = rect[0] + max(0, (rect_w - tw) // 2)
        if highlight_budgetwing:
            _draw_line_centered_budgetwing(
                draw,
                ln,
                font=font,
                x_center=rect[0] + rect_w // 2,
                y=cur_y,
                default_fill=(255, 255, 255, 245),
                brand_fill=(255, 153, 51, 248),
            )
        else:
            # shadow
            draw.text((x + shadow_off[0], cur_y + shadow_off[1]), ln, font=font, fill=(0, 0, 0, 200))
            # main
            draw.text((x, cur_y), ln, font=font, fill=(255, 255, 255, 245))
        cur_y += th + int(max(6, h * 0.006))
    img.save(out_png)
    return out_png


def _render_intro_overlay(
    out_png: Path,
    *,
    hook: str,
    title: str,
    caption: str,
    frame_size: tuple[int, int],
) -> Path:
    """Single padded overlay block: hook (top), title (main), caption (secondary)."""
    w, h = frame_size
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    max_w = int(w * 0.84)
    hook_font, hook_lines = _fit_lines(draw, hook, int(h * 0.028), max_w, max_lines=1)
    title_font, title_lines = _fit_lines(draw, title, int(h * 0.043), max_w, max_lines=2)
    cap_font, cap_lines = _fit_lines(draw, caption, int(h * 0.032), max_w, max_lines=3)

    hook_lines = [ln for ln in hook_lines if ln.strip()]
    title_lines = [ln for ln in title_lines if ln.strip()]
    cap_lines = [ln for ln in cap_lines if ln.strip()]
    if not title_lines and not cap_lines:
        img.save(out_png)
        return out_png

    line_gap = int(max(8, h * 0.006))
    block_gap_main = int(max(12, h * 0.01))
    block_gap_sub = int(max(10, h * 0.008))

    entries: list[tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont, tuple[int, int, int, int]]] = []
    # hook accent color
    for ln in hook_lines:
        entries.append((ln, hook_font, (111, 186, 255, 245)))
    if hook_lines:
        entries.append(("", hook_font, (0, 0, 0, 0)))
    for ln in title_lines:
        entries.append((ln, title_font, (255, 255, 255, 248)))
    if cap_lines:
        entries.append(("", cap_font, (0, 0, 0, 0)))
    for ln in cap_lines:
        entries.append((ln, cap_font, (236, 241, 248, 245)))

    rows: list[tuple[str, int, int, ImageFont.FreeTypeFont | ImageFont.ImageFont, tuple[int, int, int, int]]] = []
    block_w = 0
    block_h = 0
    in_title = False
    for idx, (ln, font, color) in enumerate(entries):
        if ln == "":
            if idx > 0 and idx < len(entries) - 1:
                gap = block_gap_main if hook_lines and not in_title else block_gap_sub
                block_h += gap
                in_title = True
            continue
        tw, th = _text_bbox(draw, ln, font)
        rows.append((ln, tw, th, font, color))
        block_w = max(block_w, tw)
        block_h += th
        if idx < len(entries) - 1 and entries[idx + 1][0] != "":
            block_h += line_gap

    pad_x = int(max(22, w * 0.03))
    pad_y = int(max(16, h * 0.018))
    rect_w = min(w - 40, block_w + pad_x * 2)
    rect_h = block_h + pad_y * 2
    x0 = (w - rect_w) // 2
    y0 = int(h * 0.1)
    y0 = max(10, min(y0, h - rect_h - 10))
    rect = (x0, y0, x0 + rect_w, y0 + rect_h)
    draw.rounded_rectangle(rect, radius=int(min(30, h * 0.03)), fill=(0, 0, 0, 188))

    cur_y = y0 + pad_y
    row_idx = 0
    for idx, (ln, font, color) in enumerate(entries):
        if ln == "":
            if idx > 0 and idx < len(entries) - 1:
                cur_y += block_gap_main if (hook_lines and row_idx <= len(hook_lines)) else block_gap_sub
            continue
        _, tw, th, rfont, rcolor = rows[row_idx]
        row_idx += 1
        x = x0 + (rect_w - tw) // 2
        draw.text((x + 4, cur_y + 4), ln, font=rfont, fill=(0, 0, 0, 210))
        draw.text((x, cur_y), ln, font=rfont, fill=rcolor)
        cur_y += th
        if idx < len(entries) - 1 and entries[idx + 1][0] != "":
            cur_y += line_gap

    img.save(out_png)
    return out_png


def _run_ffmpeg(cmd: list[str], context: str) -> None:
    logger.debug("ffmpeg %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()
        if len(tail) > 4000:
            tail = tail[-4000:]
        raise RuntimeError(f"ffmpeg failed ({context}) exit={proc.returncode}. stderr tail:\n{tail}")


def build_instapost_reel(
    *,
    work_dir: Path,
    clip_paths: list[Path],
    hook: str,
    title: str,
    caption: str,
    place_text: str,
    cta: str,
    music_path: Path | None,
    total_duration_seconds: float | None = None,
) -> Path:
    """
    Build one Instagram-style vertical reel with hook + title + caption intro overlay and CTA.

    The reel is generated in ``work_dir`` and returned as an MP4 path.
    """
    if not clip_paths:
        raise RuntimeError("No clip paths provided.")
    frame_size = config.REEL_SIZE
    w, h = frame_size

    work_dir.mkdir(parents=True, exist_ok=True)

    if total_duration_seconds is None:
        total_duration_seconds = float(random.randint(18, 20)) + random.choice([0.0, 0.25, 0.5])
    total_duration_seconds = max(16.0, min(20.0, float(total_duration_seconds)))

    n = min(5, max(3, len(clip_paths)))
    # Use up to n segments: if more clips, trim; if fewer, repeat.
    if len(clip_paths) >= n:
        segments = list(clip_paths[:n])
    else:
        segments = list(clip_paths)
        while len(segments) < n:
            segments.append(random.choice(clip_paths))

    seg_dur = total_duration_seconds / n
    fade_dur = max(0.25, min(0.6, seg_dur / 5.0))

    title_window_end = min(4.8, total_duration_seconds * 0.34)
    cta_dur = min(3.0, max(2.0, total_duration_seconds * 0.18))
    cta_start = max(0.0, total_duration_seconds - cta_dur)

    # Render overlays (full-frame RGBA PNGs).
    intro_png = work_dir / "overlay_intro.png"
    place_png = work_dir / "overlay_place.png"
    cta_png = work_dir / "overlay_cta.png"

    _render_intro_overlay(
        intro_png,
        hook=hook,
        title=title,
        caption=caption,
        frame_size=frame_size,
    )
    _render_overlay_full_frame(
        place_png,
        text=place_text,
        frame_size=frame_size,
        y=int(h * 0.70),
        font_size=int(h * 0.033),
        max_width_ratio=0.88,
        max_lines=3,
        bg_alpha=172,
        highlight_budgetwing=True,
    )
    _render_overlay_full_frame(
        cta_png,
        text=cta,
        frame_size=frame_size,
        y=int(h * 0.78),
        font_size=int(h * 0.042),
        max_width_ratio=0.86,
        max_lines=2,
        bg_alpha=150,
    )

    reel_out = work_dir / "reel.mp4"

    # Inputs ordering:
    # 0..N-1 clips
    # N..N+2 overlay images
    # optional music
    cmd: list[str] = [_ensure_ffmpeg(), "-y"]

    clip_input_indices: list[int] = []
    for p in segments:
        clip_input_indices.append(len(cmd))  # not used; indices are logical, not CLI offsets
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            cmd.extend(["-loop", "1", "-i", str(p)])
        else:
            cmd.extend(["-i", str(p)])

    intro_idx = len(segments)
    place_idx = intro_idx + 1
    cta_idx = intro_idx + 2

    cmd.extend(["-loop", "1", "-i", str(intro_png)])
    cmd.extend(["-loop", "1", "-i", str(place_png)])
    cmd.extend(["-loop", "1", "-i", str(cta_png)])

    music_idx = None
    if music_path is not None and music_path.is_file():
        music_idx = cta_idx + 1
        cmd.extend(["-stream_loop", "-1", "-i", str(music_path)])

    # Build filtergraph.
    # Clip streams become v0..v{n-1}
    clip_filters: list[str] = []
    v_labels: list[str] = []

    for i in range(n):
        in_vid = f"[{i}:v]"
        out_v = f"[v{i}]"
        # Scale + center-crop to vertical.
        clip_filters.append(
            f"""{in_vid}scale=w={w}:h={h}:force_original_aspect_ratio=increase,crop=w={w}:h={h}:x=(in_w-out_w)/2:y=(in_h-out_h)/2,setsar=1,format=yuv420p,fps=30,trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS,fade=t=in:st=0:d={fade_dur:.3f},fade=t=out:st={(seg_dur - fade_dur):.3f}:d={fade_dur:.3f} {out_v}"""
        )
        v_labels.append(out_v)

    transition_types = [
        "fade",
        "slideleft",
        "slideright",
        "slideup",
        "slidedown",
        "wipeleft",
        "wiperight",
    ]
    xfade_dur = max(0.22, min(0.55, seg_dur * 0.22))

    transition_lines: list[str] = []
    current = v_labels[0]
    current_duration = seg_dur
    for i in range(1, n):
        nxt = v_labels[i]
        out = f"[vx{i}]"
        tr = random.choice(transition_types)
        offset = max(0.01, current_duration - xfade_dur)
        transition_lines.append(
            f"{current}{nxt}xfade=transition={tr}:duration={xfade_dur:.3f}:offset={offset:.3f}{out}"
        )
        current = out
        current_duration = current_duration + seg_dur - xfade_dur

    base_video_label = current

    intro_in = f"[{intro_idx}:v]"
    place_in = f"[{place_idx}:v]"
    cta_in = f"[{cta_idx}:v]"

    filter_parts = []
    filter_parts.extend(clip_filters)
    filter_parts.extend(transition_lines)

    filter_parts.append(
        f"{intro_in}format=rgba[intro];{place_in}format=rgba[place];{cta_in}format=rgba[cta]"
    )

    filter_parts.append(
        f"""{base_video_label}[intro]overlay=x=0:y=0:enable='between(t,0,{title_window_end:.3f})'[v0]"""
    )
    filter_parts.append(
        f"""[v0][place]overlay=x=0:y=0:enable='between(t,{title_window_end:.3f},{cta_start:.3f})'[v2]"""
    )
    filter_parts.append(
        f"""[v2][cta]overlay=x=0:y=0:enable='between(t,{cta_start:.3f},{total_duration_seconds:.3f})'[vout]"""
    )

    cmd.extend(["-filter_complex", ";".join(filter_parts)])

    cmd.extend(["-map", "[vout]"])

    if music_idx is not None:
        # music input is last; pick its first audio stream.
        cmd.extend(["-map", f"{music_idx}:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest"])
    else:
        cmd.extend(["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20"])

    cmd.extend(["-t", f"{total_duration_seconds:.3f}", "-movflags", "+faststart", str(reel_out)])

    _run_ffmpeg(cmd, "instapost reel")
    return reel_out


def _ensure_ffmpeg() -> str:
    configured = config.resolve_ffmpeg_executable()
    if configured:
        return configured
    import shutil

    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg not found. Set FFMPEG_PATH to your install folder or add ffmpeg.exe to your PATH. "
            "See https://ffmpeg.org/download.html"
        )
    return exe

