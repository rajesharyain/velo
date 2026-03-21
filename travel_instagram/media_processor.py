"""
Image carousel rendering (PIL) and vertical reel assembly (FFmpeg).
"""

from __future__ import annotations

import logging
import random
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Sequence

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from travel_instagram import config

logger = logging.getLogger(__name__)

# Cross-platform: try common fonts; fallback to default bitmap font.
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        p = Path(path)
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _ensure_ffmpeg() -> str:
    configured = config.resolve_ffmpeg_executable()
    if configured:
        return configured
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg not found. Set FFMPEG_PATH to your install folder (e.g. R:\\projects\\ffmpeg-8.1) "
            "or the full path to ffmpeg.exe, or add FFmpeg to your system PATH. "
            "See https://ffmpeg.org/download.html"
        )
    return exe


def _format_ffmpeg_exit_code(rc: int | None) -> str:
    """Explain huge Windows return codes (unsigned 32-bit wrap of negative values)."""
    if rc is None:
        return "unknown"
    if rc > 2**31:
        signed = rc - 2**32
        return f"{rc} (often {signed} as signed 32-bit)"
    return str(rc)


def _ffmpeg_stderr_tail(stderr: str | None, limit: int = 6000) -> str:
    if not stderr or not stderr.strip():
        return "(no stderr)"
    s = stderr.strip()
    if len(s) > limit:
        return "…" + s[-limit:]
    return s


def _run_ffmpeg_cmd(cmd: list[str], context: str) -> None:
    """Run ffmpeg; on failure raise with exit code and stderr (Windows-safe decoding)."""
    logger.debug("ffmpeg %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        err = _ffmpeg_stderr_tail(proc.stderr)
        logger.error("ffmpeg failed [%s] code=%s\n%s", context, proc.returncode, err)
        raise RuntimeError(
            f"ffmpeg failed ({context}) exit={_format_ffmpeg_exit_code(proc.returncode)}. "
            f"Last stderr:\n{err}"
        )


def download_binary(url: str, dest: Path, client: httpx.Client | None = None) -> Path:
    """Download URL to dest; return dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    c = client or httpx.Client(timeout=120.0, follow_redirects=True)
    try:
        r = c.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    finally:
        if own_client:
            c.close()
    return dest


def _cover_crop(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale image to cover `size`, center crop."""
    tw, th = size
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = int(w * scale), int(h * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def _darken_backdrop(base: Image.Image, amount: float = 0.45) -> Image.Image:
    """Slight darken + blur for text legibility."""
    overlay = Image.new("RGBA", base.size, (0, 0, 0, int(255 * amount)))
    blurred = base.filter(ImageFilter.GaussianBlur(radius=2))
    out = Image.alpha_composite(blurred.convert("RGBA"), overlay)
    return out.convert("RGB")


def render_text_slide(
    image_path: Path,
    out_path: Path,
    primary_text: str,
    secondary_text: str | None = None,
    size: tuple[int, int] | None = None,
) -> Path:
    """
    Create a JPEG with centered text over a darkened region.
    `primary_text` is larger; `secondary_text` optional subtitle.
    """
    size = size or config.CAROUSEL_SIZE
    tw, th = size
    im = Image.open(image_path).convert("RGB")
    im = _cover_crop(im, size)
    canvas = _darken_backdrop(im, amount=0.42)

    draw = ImageDraw.Draw(canvas)
    title_font = _find_font(52)
    body_font = _find_font(34)

    def wrap_lines(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> list[str]:
        if font == ImageFont.load_default():
            return textwrap.wrap(text, width=28)
        lines: list[str] = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            cur: list[str] = []
            for w in words:
                trial = (" ".join(cur + [w])).strip()
                bbox = draw.textbbox((0, 0), trial, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    cur.append(w)
                else:
                    if cur:
                        lines.append(" ".join(cur))
                    cur = [w]
            if cur:
                lines.append(" ".join(cur))
        return lines

    max_w = tw - 120
    p_lines = wrap_lines(primary_text.strip(), title_font, max_w)
    s_lines = wrap_lines(secondary_text.strip(), body_font, max_w) if secondary_text else []

    line_gap_title = 8
    line_gap_body = 6
    title_h = sum(
        draw.textbbox((0, 0), ln, font=title_font)[3]
        - draw.textbbox((0, 0), ln, font=title_font)[1]
        + line_gap_title
        for ln in p_lines
    )
    body_h = sum(
        draw.textbbox((0, 0), ln, font=body_font)[3]
        - draw.textbbox((0, 0), ln, font=body_font)[1]
        + line_gap_body
        for ln in s_lines
    ) if s_lines else 0
    gap_block = 28 if s_lines else 0
    total_h = title_h + gap_block + body_h
    y = (th - total_h) // 2

    shadow = (4, 4)

    for ln in p_lines:
        bbox = draw.textbbox((0, 0), ln, font=title_font)
        h = bbox[3] - bbox[1]
        x = (tw - (bbox[2] - bbox[0])) // 2
        for dx, dy in [(shadow[0], shadow[1]), (0, 0)]:
            color = (0, 0, 0) if dx else (255, 255, 255)
            draw.text((x + dx, y + dy), ln, font=title_font, fill=color)
        y += h + line_gap_title

    y += gap_block - line_gap_title if s_lines else 0

    for ln in s_lines:
        bbox = draw.textbbox((0, 0), ln, font=body_font)
        h = bbox[3] - bbox[1]
        x = (tw - (bbox[2] - bbox[0])) // 2
        for dx, dy in [(shadow[0], shadow[1]), (0, 0)]:
            color = (0, 0, 0) if dx else (245, 245, 245)
            draw.text((x + dx, y + dy), ln, font=body_font, fill=color)
        y += h + line_gap_body

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Baseline JPEG (no optimize) decodes more reliably in FFmpeg image / concat paths.
    canvas.save(out_path, "JPEG", quality=92, optimize=False, subsampling=2)
    return out_path


def build_carousel_slides(
    work_dir: Path,
    content: dict[str, Any],
    image_paths_by_dest_index: Sequence[list[Path]],
) -> list[Path]:
    """
    Build 5–10 JPEG slides (1080×1350): hook, one slide per destination (caption),
    optional bonus photo slides, hashtag slide, CTA.

    `image_paths_by_dest_index` aligns with `content['destinations']` indices.
    """
    hook = str(content.get("hook", ""))
    cta = str(content.get("cta", ""))
    tags = content.get("hashtags") or []
    hashtag_line = " ".join(f"#{t}" for t in tags[:12]) if tags else "#travel #wanderlust"

    destinations: list[dict[str, Any]] = list(content.get("destinations") or [])
    all_images: list[Path] = [p for group in image_paths_by_dest_index for p in group]
    if not all_images:
        raise RuntimeError("No images available for carousel.")

    slides_spec: list[tuple[str, str | None, Path]] = []

    slides_spec.append((hook, None, random.choice(all_images)))

    for i, dest in enumerate(destinations):
        name = str(dest.get("destination", ""))
        cap = str(dest.get("caption", ""))
        imgs = list(image_paths_by_dest_index[i]) if i < len(image_paths_by_dest_index) else []
        img = imgs[0] if imgs else random.choice(all_images)
        slides_spec.append((name, cap, img))

    for i, dest in enumerate(destinations):
        imgs = list(image_paths_by_dest_index[i]) if i < len(image_paths_by_dest_index) else []
        if len(imgs) < 2:
            continue
        if len(slides_spec) >= 9:
            break
        name = str(dest.get("destination", ""))
        slides_spec.append((name, "Save this for later", imgs[1]))

    if len(slides_spec) < 9:
        slides_spec.append((hashtag_line, None, random.choice(all_images)))

    slides_spec.append((cta, None, random.choice(all_images)))

    while len(slides_spec) < 5:
        slides_spec.insert(-1, ("Discover more", None, random.choice(all_images)))

    if len(slides_spec) > 10:
        core_end = 1 + len(destinations)
        core = slides_spec[:core_end]
        tail = slides_spec[-2:]
        merged = core + tail
        while len(merged) < 5:
            merged.insert(-1, ("Travel inspo", None, random.choice(all_images)))
        slides_spec = merged[:10]

    out_paths: list[Path] = []
    for idx, (primary, secondary, img) in enumerate(slides_spec):
        out = work_dir / f"slide_{idx + 1:02d}.jpg"
        render_text_slide(img, out, primary, secondary)
        out_paths.append(out)

    return out_paths


def _center_text(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - w / 2, cy - h / 2), text, font=font, fill=fill)


def _render_reel_brand_overlay_png(path: Path, brand_text: str) -> None:
    """Semi-transparent pill with an “info” mark + label for FFmpeg overlay."""
    font = _find_font(19)
    font_i = _find_font(17)
    dummy = Image.new("RGB", (4, 4))
    dr = ImageDraw.Draw(dummy)
    bbox = dr.textbbox((0, 0), brand_text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    ih = 28
    gap = 10
    pad_l, pad_r, pad_v = 12, 14, 9
    w = pad_l + ih + gap + tw + pad_r
    h = max(ih + pad_v * 2, th + pad_v * 2 + 4)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w, h), radius=h // 2, fill=(18, 18, 18, 218))
    cx_i = pad_l + ih // 2
    cy_i = h // 2
    ri = ih // 2 - 2
    draw.ellipse(
        (cx_i - ri, cy_i - ri, cx_i + ri, cy_i + ri),
        fill=(255, 255, 255, 240),
    )
    _center_text(draw, cx_i, cy_i, "i", font_i, (25, 25, 28))
    draw.text((pad_l + ih + gap, (h - th) // 2 - 2), brand_text, font=font, fill=(255, 255, 255, 245))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")


def _apply_reel_brand_overlay(video_in: Path, video_out: Path, work_dir: Path, brand: str) -> None:
    """Composite brand pill bottom-left (Instagram-style). Re-encodes video (no audio)."""
    b = (brand or "").strip()
    if not b:
        shutil.copy(video_in, video_out)
        return
    png = work_dir / "reel_brand_overlay.png"
    _render_reel_brand_overlay_png(png, b)
    exe = _ensure_ffmpeg()
    cmd = [
        exe,
        "-y",
        "-i",
        str(video_in),
        "-i",
        str(png),
        "-filter_complex",
        "[0:v][1:v]overlay=20:main_h-overlay_h-32",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-an",
        str(video_out),
    ]
    _run_ffmpeg_cmd(cmd, "reel brand overlay")


def _mux_music(video_path: Path, music_path: Path, out: Path) -> None:
    exe = _ensure_ffmpeg()
    cmd = [
        exe,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(music_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out),
    ]
    _run_ffmpeg_cmd(cmd, "reel audio mux")


def _reel_frame_rgb24(src: Path, size: tuple[int, int]) -> bytes:
    """Load image with PIL; return raw RGB24 bytes (width × height × 3) for FFmpeg rawvideo."""
    w, h = size
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        frame = _cover_crop(rgb, (w, h))
        if frame.size != (w, h):
            frame = frame.resize((w, h), Image.Resampling.LANCZOS)
        return frame.tobytes()


def _encode_reel_rawvideo_to_mp4(
    stills_rgb: list[bytes],
    seconds_each: float,
    out_mp4: Path,
    context: str,
) -> None:
    """
    Feed RGB24 frames on stdin; FFmpeg never touches JPEG/PNG demuxers (fixes
    “broken data stream when reading image file” on Windows builds).
    """
    w, h = config.REEL_SIZE
    fps = 30
    frames_each = max(1, int(round(seconds_each * fps)))
    exe = _ensure_ffmpeg()
    cmd = [
        exe,
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{w}x{h}",
        "-framerate",
        str(fps),
        "-i",
        "-",
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
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    err = b""
    try:
        assert proc.stdin is not None
        for rgb in stills_rgb:
            for _ in range(frames_each):
                proc.stdin.write(rgb)
        proc.stdin.close()
        err = proc.stderr.read() if proc.stderr else b""
        proc.wait(timeout=600)
    except BrokenPipeError as e:
        err = proc.stderr.read() if proc.stderr else b""
        proc.wait(timeout=5)
        raise RuntimeError(
            f"ffmpeg rawvideo pipe closed early ({context}). "
            f"stderr:\n{err.decode('utf-8', errors='replace')[-4000:]}"
        ) from e
    except Exception:
        proc.kill()
        raise

    if proc.returncode != 0:
        tail = err.decode("utf-8", errors="replace")
        logger.error("ffmpeg rawvideo failed [%s]\n%s", context, tail[-4000:])
        raise RuntimeError(
            f"ffmpeg failed ({context}) exit={_format_ffmpeg_exit_code(proc.returncode)}. "
            f"Last stderr:\n{_ffmpeg_stderr_tail(tail)}"
        )


def build_reel_from_images(
    work_dir: Path,
    image_paths: Sequence[Path],
    out_mp4: Path,
    *,
    music_path: Path | None = None,
) -> Path:
    """
    Combine ``REEL_FRAME_COUNT`` stills into one vertical MP4 (images only).

    PIL decodes each file; raw RGB24 is streamed into FFmpeg **rawvideo** stdin.
    No image2/concat demuxer — avoids broken image streams on some FFmpeg builds.
    """
    n = max(1, config.REEL_FRAME_COUNT)
    raw = [Path(p) for p in image_paths if Path(p).is_file()]
    if not raw:
        raise RuntimeError("No image files supplied for reel.")

    pool = list(raw)
    chosen: list[Path] = [pool[i % len(pool)] for i in range(n)]

    w, h = config.REEL_SIZE
    total = max(5.0, min(30.0, config.REEL_TOTAL_SECONDS))
    per = total / n

    stills_rgb: list[bytes] = []
    for src in chosen:
        try:
            stills_rgb.append(_reel_frame_rgb24(src, (w, h)))
        except OSError as e:
            raise RuntimeError(f"Could not read image for reel: {src}") from e

    work_dir.mkdir(parents=True, exist_ok=True)
    no_audio = work_dir / "reel_noaudio.mp4"
    _encode_reel_rawvideo_to_mp4(stills_rgb, per, no_audio, "reel rawvideo stdin")

    branded = work_dir / "reel_branded_noaudio.mp4"
    _apply_reel_brand_overlay(no_audio, branded, work_dir, config.REEL_BRAND_TEXT)

    if music_path is not None and music_path.is_file():
        _mux_music(branded, music_path, out_mp4)
    else:
        shutil.copy(branded, out_mp4)

    return out_mp4
