"""
Image carousel rendering (PIL) and vertical reel assembly (FFmpeg).
"""

from __future__ import annotations

import logging
import random
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Sequence

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from travel_instagram import config

logger = logging.getLogger(__name__)

# Cross-platform: title = boldest first; body = semibold/regular for clearer hierarchy.
_TITLE_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_BODY_CORE = [
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_BODY_FONT_CANDIDATES = _BODY_CORE + [p for p in _TITLE_FONT_CANDIDATES if p not in _BODY_CORE]


def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Legacy: prefer bold (same as title)."""
    return _find_title_font(size)


def _find_title_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _TITLE_FONT_CANDIDATES:
        p = Path(path)
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _find_body_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _BODY_FONT_CANDIDATES:
        p = Path(path)
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _typography_scale(th: int, ref: int = 1920) -> float:
    """Clamp scale so exports stay readable on short/tall canvases."""
    return max(0.88, min(1.22, th / float(ref)))


def _scaled_title_sizes(th: int) -> list[int]:
    """~25% larger than previous ladder at 1920; scales with slide height."""
    s = _typography_scale(th) * 1.26 * float(getattr(config, "CAROUSEL_TITLE_FONT_SCALE", 1.0))
    bases = [48, 42, 36, 32, 28, 24]
    out = [max(24, int(round(b * s))) for b in bases]
    for i in range(1, len(out)):
        if out[i] >= out[i - 1]:
            out[i] = out[i - 1] - 2
    return out


def _scaled_body_sizes(th: int) -> list[int]:
    """~12% larger than previous ladder at 1920; scales with slide height."""
    s = _typography_scale(th) * 1.12 * float(getattr(config, "CAROUSEL_BODY_FONT_SCALE", 1.0))
    bases = [32, 28, 26, 24]
    out = [max(20, int(round(b * s))) for b in bases]
    for i in range(1, len(out)):
        if out[i] >= out[i - 1]:
            out[i] = out[i - 1] - 2
    return out


def _slide_already_shows_brand_url(primary: str, secondary: str | None) -> bool:
    """True if primary/caption already contains the site (skip duplicate footer line)."""
    blob = f"{primary}\n{secondary or ''}".lower()
    return "budgetwing.com" in blob


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


def _split_long_token(
    token: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    draw: ImageDraw.ImageDraw,
    max_width: int,
) -> list[str]:
    """Break a single word/token across lines when it is wider than ``max_width``."""
    if not token:
        return []
    if draw.textbbox((0, 0), token, font=font)[2] - draw.textbbox((0, 0), token, font=font)[0] <= max_width:
        return [token]
    parts: list[str] = []
    acc = ""
    for ch in token:
        trial = acc + ch
        if draw.textbbox((0, 0), trial, font=font)[2] - draw.textbbox((0, 0), trial, font=font)[0] <= max_width:
            acc = trial
        else:
            if acc:
                parts.append(acc)
            acc = ch
    if acc:
        parts.append(acc)
    return parts if parts else [token[:32]]


def _carousel_text_max_width(tw: int) -> int:
    """
    Max line width for text on a 9:16 carousel slide (same aspect as Reels).

    When ``CAROUSEL_SIZE`` matches ``REEL_SIZE``, the full slide width is visible in Reels.
    """
    cw, ch = config.CAROUSEL_SIZE
    rw, rh = config.REEL_SIZE
    if cw <= 0 or rh <= 0:
        return max(200, tw - 120)
    # Same width canvas (1080): visible width ratio = ch/rh when crop is horizontal-only
    visible_ratio = (ch * rw) / (rh * cw)
    inner = int(tw * visible_ratio) - 88
    return max(240, min(tw - 80, inner))


def _draw_slide_footer_brand(
    draw: ImageDraw.ImageDraw,
    tw: int,
    th: int,
    *,
    y_top: int,
) -> None:
    """Horizontally centered brand line below the caption stack (``y_top`` = first pixel row for text)."""
    label = (config.REEL_BRAND_TEXT or "").strip()
    if not label:
        return
    base_px = max(24, min(40, int(th * 0.021)))
    size = base_px + 1
    font = _find_body_font(size)
    bbox = draw.textbbox((0, 0), label, font=font)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(8, (tw - bw) // 2)
    y = int(y_top)
    y = max(8, min(y, th - bh - 8))
    rgb = config.BRAND_DOMAIN_RGB
    sw = max(2, min(5, th // 380))
    draw.text(
        (x, y),
        label,
        font=font,
        fill=rgb,
        stroke_width=sw,
        stroke_fill=(14, 14, 18),
    )


def _destination_about_text(dest: dict[str, Any]) -> str:
    """Middle-lower caption: descriptive copy + vibe + scenery (for Reels context)."""
    cap = str(dest.get("caption", "")).strip()
    vibe = str(dest.get("vibe", "")).strip()
    scapes = dest.get("scape_types") or []
    scenic = ", ".join(str(s).replace("_", " ") for s in scapes[:6] if s) if scapes else ""
    chunks: list[str] = []
    if cap:
        chunks.append(cap)
    if vibe:
        vl = vibe.lower()
        if not cap or vl not in cap.lower():
            chunks.append(vibe if vibe.endswith(".") else vibe + ".")
    if scenic:
        chunks.append(f"Scenery: {scenic}.")
    return " ".join(chunks).strip()


_BRAND_RE = re.compile(r"(budgetwing\.com)", re.IGNORECASE)


def _segment_line_brand(line: str) -> list[tuple[str, bool]]:
    parts = _BRAND_RE.split(line)
    out: list[tuple[str, bool]] = []
    for p in parts:
        if p == "":
            continue
        out.append((p, p.lower() == "budgetwing.com"))
    return out if out else [(line, False)]


def _rich_segments_width(
    draw: ImageDraw.ImageDraw,
    parts: list[tuple[str, bool]],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> int:
    w = 0
    for text, _ in parts:
        if not text:
            continue
        b = draw.textbbox((0, 0), text, font=font)
        w += b[2] - b[0]
    return w


def _draw_map_pin(draw: ImageDraw.ImageDraw, x_left: int, y_top: int, pin_h: int) -> int:
    """Draw a simple location pin; returns x after pin + gap."""
    fill = (56, 142, 240)
    outline = (18, 40, 70)
    w_box = max(22, pin_h - 6)
    cx = x_left + w_box // 2
    r = max(7, pin_h // 4)
    cy = y_top + r + 1
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=1)
    tip_y = y_top + pin_h - 1
    spread = r + 5
    draw.polygon(
        [(cx, tip_y), (cx - spread, cy + r - 1), (cx + spread, cy + r - 1)],
        fill=fill,
        outline=outline,
    )
    return x_left + w_box + 14


def _draw_brand_line_centered(
    draw: ImageDraw.ImageDraw,
    canvas_w: int,
    y: int,
    line: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    shadow: tuple[int, int],
    *,
    is_primary: bool,
    location_pin: bool = False,
) -> int:
    """Centered line with optional map pin; ``budgetwing.com`` accent + underline."""
    parts = _segment_line_brand(line)
    default_rgb = (255, 255, 255) if is_primary else (245, 245, 245)
    brand_rgb = config.BRAND_DOMAIN_RGB
    text_w = _rich_segments_width(draw, parts, font)
    ref = draw.textbbox((0, 0), line or " ", font=font)
    line_h = ref[3] - ref[1]
    pin_h = min(40, max(28, int(line_h * 1.2)))
    pin_extra = 0
    if location_pin:
        w_box = max(22, pin_h - 6)
        pin_extra = w_box + 14
    total_w = pin_extra + text_w
    x = (canvas_w - total_w) // 2
    if location_pin:
        y_pin = y + max(0, (line_h - pin_h) // 2)
        x = _draw_map_pin(draw, x, y_pin, pin_h)
    if is_primary:
        sx, sy = shadow[0], shadow[1]
        shadow_passes: list[tuple[int, int]] = [(sx, sy), (max(1, sx // 2), max(1, sy // 2)), (0, 0)]
    else:
        shadow_passes = [(shadow[0], shadow[1]), (0, 0)]

    for text, is_br in parts:
        if not text:
            continue
        fill = brand_rgb if is_br else default_rgb
        for dx, dy in shadow_passes:
            c = (0, 0, 0) if (dx, dy) != (0, 0) else fill
            draw.text((x + dx, y + dy), text, font=font, fill=c)
        if is_br:
            bb = draw.textbbox((x, y), text, font=font)
            und_y = bb[3] + 2
            draw.line((bb[0], und_y, bb[2], und_y), fill=brand_rgb, width=max(2, line_h // 12))
        b = draw.textbbox((0, 0), text, font=font)
        x += b[2] - b[0]
    return line_h


def _darken_backdrop(
    base: Image.Image,
    amount: float = 0.45,
    *,
    blur_radius: float = 2.0,
) -> Image.Image:
    """Light darken + optional blur for text legibility (keep ``amount`` low for bright photos)."""
    overlay = Image.new("RGBA", base.size, (0, 0, 0, int(255 * amount)))
    blurred = (
        base.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        if blur_radius > 0
        else base
    )
    out = Image.alpha_composite(blurred.convert("RGBA"), overlay)
    return out.convert("RGB")


def render_text_slide(
    image_path: Path,
    out_path: Path,
    primary_text: str,
    secondary_text: str | None = None,
    size: tuple[int, int] | None = None,
    *,
    vertical_bias_up_ratio: float | None = None,
    location_pin: bool = False,
) -> Path:
    """
    Create a JPEG with text over the photo.

    - Primary and secondary are **stacked and vertically centered** (with light top/bottom padding).
    - Horizontal width respects reel-safe wrapping + side inset.
    - ``location_pin``: map pin before the first primary line (destination titles).
    - ``budgetwing.com`` in body copy is accent + underline; a small footer URL sits below the caption
      unless the slide text already includes that domain (no duplicate).
    """
    size = size or config.CAROUSEL_SIZE
    tw, th = size
    im = Image.open(image_path).convert("RGB")
    im = _cover_crop(im, size)
    canvas = _darken_backdrop(im, amount=0.17, blur_radius=1.0)

    draw = ImageDraw.Draw(canvas)
    inset = max(0.82, min(1.0, float(config.REEL_TEXT_SIDE_INSET_RATIO)))
    max_w = max(200, int(_carousel_text_max_width(tw) * inset))
    block_pad = max(14, int(th * 0.02))

    def wrap_lines(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> list[str]:
        if font == ImageFont.load_default():
            return textwrap.wrap(text, width=max(12, max_width // 14))
        lines: list[str] = []
        for paragraph in text.replace("\r\n", "\n").split("\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            words = paragraph.split()
            cur: list[str] = []
            for w in words:
                for piece in _split_long_token(w, font, draw, max_width):
                    trial = (" ".join(cur + [piece])).strip()
                    bbox = draw.textbbox((0, 0), trial, font=font)
                    if bbox[2] - bbox[0] <= max_width:
                        cur.append(piece)
                    else:
                        if cur:
                            lines.append(" ".join(cur))
                        cur = [piece]
            if cur:
                lines.append(" ".join(cur))
        while lines and lines[-1] == "":
            lines.pop()
        return lines

    primary = (primary_text or "").strip() or "."
    title_sizes = _scaled_title_sizes(th)
    p_lines: list[str] = []
    title_font = _find_title_font(title_sizes[0])
    for tsize in title_sizes:
        title_font = _find_title_font(tsize)
        p_lines = wrap_lines(primary, title_font, max_w)
        if not p_lines:
            p_lines = [primary[:80]]
        longest = 0
        for ln in p_lines:
            if not ln:
                continue
            b = draw.textbbox((0, 0), ln, font=title_font)
            longest = max(longest, b[2] - b[0])
        if longest <= max_w and len([x for x in p_lines if x]) <= 7:
            break

    body_sizes = _scaled_body_sizes(th)
    s_lines: list[str] = []
    body_font = _find_body_font(body_sizes[0])
    if secondary_text and str(secondary_text).strip():
        sec = str(secondary_text).strip()
        for bsize in body_sizes:
            body_font = _find_body_font(bsize)
            s_lines = wrap_lines(sec, body_font, max_w)
            widths_b = [
                draw.textbbox((0, 0), ln, font=body_font)[2]
                - draw.textbbox((0, 0), ln, font=body_font)[0]
                for ln in s_lines
                if ln
            ]
            longest = max(widths_b) if widths_b else 0
            if longest <= max_w and len([x for x in s_lines if x]) <= 8:
                break

    line_gap_title = max(12, int(round(12 * _typography_scale(th))))
    line_gap_body = max(10, int(round(10 * _typography_scale(th))))
    def line_height(ln: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, gap: int) -> int:
        if not ln:
            return gap // 2
        b = draw.textbbox((0, 0), ln, font=font)
        return b[3] - b[1] + gap

    title_h = sum(line_height(ln, title_font, line_gap_title) for ln in p_lines)
    body_h = sum(line_height(ln, body_font, line_gap_body) for ln in s_lines) if s_lines else 0
    gap_block = max(32, int(th * 0.022)) if s_lines else 0
    brand_label = (config.REEL_BRAND_TEXT or "").strip()
    show_footer_brand = bool(brand_label) and not _slide_already_shows_brand_url(
        primary,
        secondary_text,
    )
    gap_brand = max(24, int(th * 0.022))
    footer_bh = 0
    if show_footer_brand:
        fb = max(24, min(40, int(th * 0.021))) + 1
        ff = _find_body_font(fb)
        _bb = draw.textbbox((0, 0), brand_label, font=ff)
        footer_bh = _bb[3] - _bb[1]
    footer_extra = (gap_brand + footer_bh) if show_footer_brand else 0
    # Total stack: title + (caption) + optional footer URL (used to center the full column)
    total_h = title_h + (gap_block - line_gap_title if s_lines else 0) + body_h + footer_extra

    safe_top = max(8, int(th * max(0.0, min(0.2, float(config.REEL_TEXT_SAFE_TOP_RATIO)))))
    safe_bot = max(8, int(th * max(0.0, min(0.35, float(config.REEL_TEXT_SAFE_BOTTOM_RATIO)))))
    content_top = safe_top + block_pad
    content_bot = th - safe_bot - block_pad

    shadow_title = (6, 6)
    shadow_body = (5, 5)

    def draw_primary_at(y_start: int) -> int:
        y = y_start
        for i, ln in enumerate(p_lines):
            if not ln:
                y += line_gap_title // 2
                continue
            h = _draw_brand_line_centered(
                draw,
                tw,
                y,
                ln,
                title_font,
                shadow_title,
                is_primary=True,
                location_pin=location_pin and i == 0,
            )
            y += h + line_gap_title
        return y

    def draw_secondary_at(y_start: int) -> int:
        y = y_start
        for ln in s_lines:
            if not ln:
                y += line_gap_body // 2
                continue
            h = _draw_brand_line_centered(
                draw,
                tw,
                y,
                ln,
                body_font,
                shadow_body,
                is_primary=False,
                location_pin=False,
            )
            y += h + line_gap_body
        return y

    bias = (
        float(vertical_bias_up_ratio)
        if vertical_bias_up_ratio is not None
        else float(config.CAROUSEL_TEXT_BIAS_UP_RATIO)
    )
    bias = max(0.0, min(0.2, bias))
    y = content_top + max(0, (content_bot - content_top - total_h) // 2)
    y = max(content_top, min(y, content_bot - total_h))
    y = max(safe_top, int(y - th * bias))

    mw_text = 0
    _dummy = ImageDraw.Draw(Image.new("RGB", (tw, th)))
    for ln in p_lines:
        if ln:
            bb = _dummy.textbbox((0, 0), ln, font=title_font)
            mw_text = max(mw_text, bb[2] - bb[0])
    for ln in s_lines:
        if ln:
            bb = _dummy.textbbox((0, 0), ln, font=body_font)
            mw_text = max(mw_text, bb[2] - bb[0])
    if mw_text > 0 and total_h > 0:
        panel_rx = max(22, int(tw * 0.04))
        panel_ry = max(18, int(th * 0.024))
        bw = min(tw - 28, mw_text + panel_rx * 2)
        bh = total_h + panel_ry * 2
        px0 = (tw - bw) // 2
        py0 = max(safe_top - 2, y - panel_ry)
        if py0 + bh > th - safe_bot + 12:
            py0 = max(safe_top, th - safe_bot - bh)
        palpha = int(getattr(config, "CAROUSEL_TEXT_PANEL_ALPHA", 218))
        panel_layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        pd = ImageDraw.Draw(panel_layer)
        pd.rounded_rectangle(
            (px0, py0, px0 + bw, py0 + bh),
            radius=int(min(34, th * 0.032)),
            fill=(12, 16, 24, palpha),
        )
        canvas = Image.alpha_composite(canvas.convert("RGBA"), panel_layer).convert(
            "RGB"
        )
        draw = ImageDraw.Draw(canvas)

    y_after_primary = draw_primary_at(y)
    if s_lines:
        y_body = y + title_h + gap_block - line_gap_title
        y_after_caption = draw_secondary_at(y_body)
    else:
        y_after_caption = y_after_primary

    if show_footer_brand:
        _draw_slide_footer_brand(draw, tw, th, y_top=y_after_caption + gap_brand)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Baseline JPEG (no optimize) decodes more reliably in FFmpeg image / concat paths.
    canvas.save(out_path, "JPEG", quality=92, optimize=False, subsampling=2)
    return out_path


def _unique_local_images_flat(groups: Sequence[Sequence[Path]]) -> list[Path]:
    """Stable de-dupe of existing files by resolved path (one row per unique download)."""
    out: list[Path] = []
    seen: set[str] = set()
    for group in groups:
        for p in group:
            if p.is_file():
                k = str(p.resolve())
                if k not in seen:
                    seen.add(k)
                    out.append(p)
    return out


def _pick_unique_image(
    used_paths: set[str],
    preferred: Sequence[Path],
    pool: list[Path],
) -> Path | None:
    """Prefer first unused path in ``preferred``, else first unused in ``pool``."""
    for p in preferred:
        if p.is_file():
            k = str(p.resolve())
            if k not in used_paths:
                used_paths.add(k)
                return p
    for p in pool:
        if p.is_file():
            k = str(p.resolve())
            if k not in used_paths:
                used_paths.add(k)
                return p
    return None


def build_carousel_slides(
    work_dir: Path,
    content: dict[str, Any],
    image_paths_by_dest_index: Sequence[list[Path]],
    *,
    reel_theme: str = "",
) -> list[Path]:
    """
    Build 5–10 JPEG slides at ``CAROUSEL_SIZE`` (9:16): theme, destinations,
    optional bonus / hashtag / closing. **Each slide uses a unique source photo**
    (no duplicate downloads / re-use).
    """
    hook = str(content.get("hook", ""))
    first_title = (reel_theme or "").strip() or hook
    tags = content.get("hashtags") or []
    hashtag_line = " ".join(f"#{t}" for t in tags[:12]) if tags else "#travel #wanderlust"

    destinations: list[dict[str, Any]] = list(content.get("destinations") or [])
    pool = _unique_local_images_flat(image_paths_by_dest_index)
    if not pool:
        raise RuntimeError("No images available for carousel.")
    min_needed = 1 + len(destinations) + 1  # title + each destination + closing
    if len(pool) < min_needed:
        raise RuntimeError(
            f"Need at least {min_needed} unique Pexels images for this run "
            f"(title, {len(destinations)} destinations, closing); only {len(pool)} unique file(s) downloaded."
        )
    random.shuffle(pool)
    used_paths: set[str] = set()

    def need_image(preferred: Sequence[Path]) -> Path:
        im = _pick_unique_image(used_paths, preferred, pool)
        if im is None:
            raise RuntimeError(
                "Not enough unique Pexels photos for this carousel. "
                "Each slide needs a different image — try another theme or check API results."
            )
        return im

    def opt_image(preferred: Sequence[Path]) -> Path | None:
        return _pick_unique_image(used_paths, preferred, pool)

    slides_spec: list[tuple[str, str | None, Path, bool]] = []

    slides_spec.append((first_title, None, need_image([]), False))

    for i, dest in enumerate(destinations):
        name = str(dest.get("destination", ""))
        about = _destination_about_text(dest) or str(dest.get("caption", "")).strip()
        imgs = list(image_paths_by_dest_index[i]) if i < len(image_paths_by_dest_index) else []
        slides_spec.append((name, about or None, need_image(imgs), True))

    for i, dest in enumerate(destinations):
        imgs = list(image_paths_by_dest_index[i]) if i < len(image_paths_by_dest_index) else []
        if len(slides_spec) >= 9:
            break
        name = str(dest.get("destination", ""))
        about = _destination_about_text(dest)
        hint = "Save this spot for later."
        sec = f"{about} {hint}".strip() if about else hint
        bonus_pref = [imgs[1]] if len(imgs) > 1 else []
        bonus_img = opt_image(bonus_pref)
        if bonus_img is not None:
            slides_spec.append((name, sec, bonus_img, True))

    closing = (config.CAROUSEL_CLOSING_TEXT or "").strip() or hook
    closing_img = need_image([])

    if len(slides_spec) < 9:
        tag_img = opt_image([])
        if tag_img is not None:
            slides_spec.append((hashtag_line, None, tag_img, False))

    slides_spec.append((closing, None, closing_img, False))

    while len(slides_spec) < 5:
        fill = opt_image([])
        if fill is None:
            break
        slides_spec.insert(-1, ("Discover more", None, fill, False))

    if len(slides_spec) > 10:
        core_end = 1 + len(destinations)
        core = slides_spec[:core_end]
        tail = slides_spec[-2:]
        merged = core + tail
        while len(merged) < 5:
            inspo = opt_image([])
            if inspo is None:
                break
            merged.insert(-1, ("Travel inspo", None, inspo, False))
        slides_spec = merged[:10]

    bias = max(0.0, min(0.28, float(config.CAROUSEL_TEXT_BIAS_UP_RATIO)))
    out_paths: list[Path] = []
    for idx, (primary, secondary, img, use_pin) in enumerate(slides_spec):
        out = work_dir / f"slide_{idx + 1:02d}.jpg"
        render_text_slide(
            img,
            out,
            primary,
            secondary,
            vertical_bias_up_ratio=bias,
            location_pin=use_pin,
        )
        out_paths.append(out)

    return out_paths


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
        "-movflags",
        "+faststart",
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


def _reel_pick_xfade_seconds(seg_dur: float) -> float:
    """Slower, more visible cuts between reel segments (config-driven bounds)."""
    lo = float(getattr(config, "REEL_XFADE_MIN_SECONDS", 0.42))
    hi = float(getattr(config, "REEL_XFADE_MAX_SECONDS", 0.95))
    r = float(getattr(config, "REEL_XFADE_SEGMENT_RATIO", 0.34))
    x = max(lo, min(hi, seg_dur * r))
    if x >= seg_dur - 0.06:
        x = max(lo * 0.82, min(hi * 0.88, seg_dur * 0.38))
    return x


# Same family as InstaPost reels (ffmpeg xfade transition names).
_REEL_XFADE_TRANSITIONS = (
    "fade",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "wipeleft",
    "wiperight",
)


def _xfade_concat_reel_segments(
    segment_paths: Sequence[Path],
    seg_actual: float,
    xfade_dur: float,
    out_duration: float,
    out_mp4: Path,
    *,
    context: str,
    transition_style: str | None = None,
    segment_durations: Sequence[float] | None = None,
) -> None:
    """
    Chain short vertical segment MP4s with ``xfade`` transitions (InstaPost-style).

    ``seg_actual`` = duration used when ``segment_durations`` is omitted (uniform clips).

    If ``segment_durations`` is set, it must match the number of inputs and holds each
    clip's real duration (frame-quantized). Do **not** ``trim`` past file length.

    If ``transition_style`` is provided, it is used for all transitions. Otherwise, a
    random transition is picked for each boundary.
    """
    paths = [Path(p) for p in segment_paths if Path(p).is_file()]
    n = len(paths)
    if n == 0:
        raise RuntimeError("No segment files for xfade concat.")
    if n == 1:
        shutil.copy(paths[0], out_mp4)
        return

    if segment_durations is not None:
        durs = [float(x) for x in segment_durations]
        if len(durs) != n:
            raise RuntimeError(
                f"segment_durations length ({len(durs)}) must match segment count ({n})."
            )
    else:
        durs = [float(seg_actual)] * n

    exe = _ensure_ffmpeg()
    # +genpts helps short segment MP4s probe cleanly on Windows.
    cmd: list[str] = [exe, "-y", "-fflags", "+genpts"]
    for p in paths:
        cmd.extend(["-i", str(p)])

    clip_filters: list[str] = []
    v_labels: list[str] = []
    for i in range(n):
        in_vid = f"[{i}:v]"
        out_v = f"[rv{i}]"
        clip_filters.append(
            f"{in_vid}fps=30,format=yuv420p,setpts=PTS-STARTPTS{out_v}"
        )
        v_labels.append(out_v)

    transition_lines: list[str] = []
    current = v_labels[0]
    current_duration = durs[0]
    for i in range(1, n):
        nxt = v_labels[i]
        out = f"[rx{i}]"
        if transition_style is None:
            tr = random.choice(_REEL_XFADE_TRANSITIONS)
        else:
            if transition_style not in _REEL_XFADE_TRANSITIONS:
                raise RuntimeError(f"Invalid transition_style={transition_style!r}.")
            tr = transition_style
        offset = max(0.01, current_duration - xfade_dur)
        transition_lines.append(
            f"{current}{nxt}xfade=transition={tr}:duration={xfade_dur:.3f}:offset={offset:.3f}{out}"
        )
        current = out
        current_duration = current_duration + durs[i] - xfade_dur

    filter_complex = ";".join(clip_filters + transition_lines)
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            current,
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
            str(out_mp4),
        ]
    )
    _run_ffmpeg_cmd(cmd, context)


def build_reel_from_images(
    work_dir: Path,
    image_paths: Sequence[Path],
    out_mp4: Path,
    *,
    music_path: Path | None = None,
) -> Path:
    """
    Combine ``REEL_FRAME_COUNT`` stills into one vertical MP4 (images only).

    Each still is encoded via raw RGB stdin (reliable on Windows), then segments are
    joined with random ``xfade`` transitions (same style as InstaPost reels).
    """
    n_target = max(1, config.REEL_FRAME_COUNT)
    raw = [Path(p) for p in image_paths if Path(p).is_file()]
    if not raw:
        raise RuntimeError("No image files supplied for reel.")

    seen: set[str] = set()
    pool: list[Path] = []
    for p in raw:
        k = str(p.resolve())
        if k not in seen:
            seen.add(k)
            pool.append(p)
    c = min(n_target, len(pool))
    chosen = pool[:c]

    w, h = config.REEL_SIZE
    n = c
    fps = 30
    per_slide = float(getattr(config, "REEL_SECONDS_PER_SLIDE", 2.9))
    t_min = float(getattr(config, "REEL_MIN_TOTAL_SECONDS", 8))
    t_max = float(getattr(config, "REEL_MAX_TOTAL_SECONDS", 78))
    jitter = random.uniform(0.94, 1.08)
    total = max(t_min, min(t_max, n * per_slide * jitter))
    per_simple = total / float(n)

    xfade_dur = 0.0
    seg_actual = total
    if n > 1:
        per_est = total / n
        xfade_dur = _reel_pick_xfade_seconds(per_est)
        seg_dur = (total + (n - 1) * xfade_dur) / n
        if xfade_dur >= seg_dur - 0.05:
            xfade_dur = _reel_pick_xfade_seconds(seg_dur * 0.92)
            seg_dur = (total + (n - 1) * xfade_dur) / n
        frames_each = max(1, int(round(seg_dur * fps)))
        seg_actual = frames_each / float(fps)

    stills_rgb: list[bytes] = []
    for src in chosen:
        try:
            stills_rgb.append(_reel_frame_rgb24(src, (w, h)))
        except OSError as e:
            raise RuntimeError(f"Could not read image for reel: {src}") from e

    work_dir.mkdir(parents=True, exist_ok=True)
    no_audio = work_dir / "reel_noaudio.mp4"
    segment_paths: list[Path] = []

    def _mux_or_copy() -> None:
        if music_path is not None and music_path.is_file():
            _mux_music(no_audio, music_path, out_mp4)
        else:
            shutil.copy(no_audio, out_mp4)

    try:
        if n == 1:
            _encode_reel_rawvideo_to_mp4(stills_rgb, total, no_audio, "reel single")
            _mux_or_copy()
        else:
            try:
                for i, rgb in enumerate(stills_rgb):
                    seg_path = work_dir / f"_reel_xfade_seg_{i:02d}.mp4"
                    _encode_reel_rawvideo_to_mp4(
                        [rgb],
                        seg_actual,
                        seg_path,
                        f"reel segment {i + 1}/{n}",
                    )
                    segment_paths.append(seg_path)

                _xfade_concat_reel_segments(
                    segment_paths,
                    seg_actual,
                    xfade_dur,
                    total,
                    no_audio,
                    context="reel xfade concat",
                )
            except RuntimeError as e:
                logger.warning(
                    "Reel xfade failed (%s); falling back to single-pass rawvideo (hard cuts).",
                    e,
                )
                for p in segment_paths:
                    try:
                        p.unlink()
                    except OSError:
                        pass
                segment_paths.clear()
                if no_audio.is_file():
                    try:
                        no_audio.unlink()
                    except OSError:
                        pass
                _encode_reel_rawvideo_to_mp4(
                    stills_rgb,
                    per_simple,
                    no_audio,
                    "reel rawvideo fallback",
                )
            _mux_or_copy()
    finally:
        for p in segment_paths:
            try:
                p.unlink()
            except OSError:
                pass

    return out_mp4
