from __future__ import annotations

import json
import logging
import random
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from travel_instagram import config
from travel_instagram import media_processor

logger = logging.getLogger(__name__)

# Rounded panel behind caption_text (on-reel blurb). None = off; caption uses drop shadow for readability instead.
CAPTION_OVERLAY_PANEL_RGBA: tuple[int, int, int, int] | None = None

# Opening-hook overlay: location word(s) from hint highlighted in yellow; ALL-CAPS emphasis; drop shadows.
_HOOK_FILL_WHITE = (255, 255, 255, 255)
_HOOK_FILL_YELLOW = (255, 234, 60, 255)
_HOOK_SHADOW_RGBA = (0, 0, 0, 150)

# Default text anchor for manual reels (horizontal center, ~15% from top).
DEFAULT_OVERLAY_ANCHOR: tuple[float, float] = (0.5, 0.15)

# Google Fonts (OFL) — lazy-downloaded into ``travel_instagram/fonts/``.
# Titles: cinematic / bold stack. Body: clean / screen-readable stack.
_GF_RAW = "https://raw.githubusercontent.com/google/fonts/main"
_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_OVERLAY_FONT_SOURCES: tuple[tuple[str, str], ...] = (
    ("ArchivoBlack-Regular.ttf", f"{_GF_RAW}/ofl/archivoblack/ArchivoBlack-Regular.ttf"),
    ("Anton-Regular.ttf", f"{_GF_RAW}/ofl/anton/Anton-Regular.ttf"),
    ("BebasNeue-Regular.ttf", f"{_GF_RAW}/ofl/bebasneue/BebasNeue-Regular.ttf"),
    ("Oswald-Variable.ttf", f"{_GF_RAW}/ofl/oswald/Oswald%5Bwght%5D.ttf"),
    ("SpaceGrotesk-Variable.ttf", f"{_GF_RAW}/ofl/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf"),
    ("Montserrat-Variable.ttf", f"{_GF_RAW}/ofl/montserrat/Montserrat%5Bwght%5D.ttf"),
    ("Inter-Variable.ttf", f"{_GF_RAW}/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf"),
    ("DMSans-Variable.ttf", f"{_GF_RAW}/ofl/dmsans/DMSans%5Bopsz%2Cwght%5D.ttf"),
    ("Urbanist-Variable.ttf", f"{_GF_RAW}/ofl/urbanist/Urbanist%5Bwght%5D.ttf"),
    ("Poppins-Regular.ttf", f"{_GF_RAW}/ofl/poppins/Poppins-Regular.ttf"),
)

# (filename, optional axis dict for variable fonts). Static fonts use ``None``.
_TITLE_FONT_STACK: tuple[tuple[str, Mapping[str, int] | None], ...] = (
    ("ArchivoBlack-Regular.ttf", None),
    ("Anton-Regular.ttf", None),
    ("BebasNeue-Regular.ttf", None),
    ("Oswald-Variable.ttf", {"wght": 650}),
    ("SpaceGrotesk-Variable.ttf", {"wght": 620}),
    ("Montserrat-Variable.ttf", {"wght": 720}),
)
_BODY_FONT_STACK: tuple[tuple[str, Mapping[str, int] | None], ...] = (
    ("Inter-Variable.ttf", {"wght": 440, "opsz": 28}),
    ("DMSans-Variable.ttf", {"wght": 420, "opsz": 28}),
    ("Urbanist-Variable.ttf", {"wght": 450}),
    ("Poppins-Regular.ttf", None),
)


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:40] or "manual"


_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"})
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})


def _sniff_is_video_file(path: Path) -> bool:
    """Detect MP4/WebM/Matroska by magic bytes when extension lies (e.g. CDN URLs)."""
    try:
        b = path.read_bytes()[:32]
    except OSError:
        return False
    if len(b) >= 12 and b[4:8] == b"ftyp":
        return True
    if b.startswith(b"\x1a\x45\xdf\xa3"):
        return True
    if len(b) >= 12 and b.startswith(b"RIFF") and b[8:12] == b"WEBM":
        return True
    return False


def _is_video(p: Path) -> bool:
    suf = p.suffix.lower()
    if suf in _VIDEO_SUFFIXES:
        return True
    if suf in _IMAGE_SUFFIXES:
        return _sniff_is_video_file(p)
    return _sniff_is_video_file(p)


def _download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "velo-font-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310 — fixed CDN URLs only
        dest.write_bytes(resp.read())


def ensure_google_overlay_fonts() -> None:
    """Lazy-fetch overlay TTFs from the pinned Google Fonts (OFL) URLs if missing."""
    for fname, url in _OVERLAY_FONT_SOURCES:
        dest = _FONT_DIR / fname
        min_bytes = 25_000 if "Variable" in fname else 8_000
        if dest.is_file() and dest.stat().st_size > min_bytes:
            continue
        try:
            _download_file(url, dest)
            logger.info("Downloaded overlay font to %s", dest)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            logger.warning("Could not download %s (%s); using fallback fonts.", fname, e)


def _apply_font_variations(font: ImageFont.ImageFont, axes: Mapping[str, int] | None) -> None:
    if not axes:
        return
    setter = getattr(font, "set_variation_by_axes", None)
    if not callable(setter):
        return
    try:
        setter(dict(axes))
        return
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    if "wght" in axes:
        try:
            setter({"wght": int(axes["wght"])})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass


def _load_overlay_font(
    fname: str,
    size: int,
    axes: Mapping[str, int] | None,
) -> ImageFont.ImageFont | None:
    path = _FONT_DIR / fname
    if not path.is_file():
        return None
    try:
        font = ImageFont.truetype(str(path), size=size)
    except OSError:
        return None
    _apply_font_variations(font, axes)
    return font


def _try_overlay_font_stack(
    stack: Sequence[tuple[str, Mapping[str, int] | None]],
    size: int,
    *,
    system_bold_fallback: bool = False,
) -> ImageFont.ImageFont:
    ensure_google_overlay_fonts()
    for fname, axes in stack:
        f = _load_overlay_font(fname, size, axes)
        if f is not None:
            return f
    sys_paths: list[str] = []
    if system_bold_fallback:
        sys_paths.extend(
            [
                r"C:\Windows\Fonts\segoeuib.ttf",
                r"C:\Windows\Fonts\arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        )
    sys_paths.extend(
        [
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for p in sys_paths:
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_map_pin(
    draw: ImageDraw.ImageDraw,
    pin_cx: int,
    line_center_y: int,
    line_h: int,
    *,
    frame_h: int,
) -> None:
    """
    Lollipop-style location pin: glossy red head, yellow stem (high visibility), rings at base.
    Vertically aligned with the first title line (head sits slightly above line center).
    """
    r = max(7, min(17, int(line_h * 0.34)))
    brown = (44, 36, 40, 255)
    red = (232, 58, 62, 255)
    red_hi = (255, 140, 145, 235)
    red_lo = (168, 38, 48, 230)
    stem_yellow = (255, 234, 60, 255)
    stem_w = max(4, min(8, r // 2))

    # Head center on the title line midline (rings extend below).
    cy = int(line_center_y)
    cy = max(r + 6, min(cy, frame_h - int(r * 3.5)))

    # Head
    draw.ellipse(
        (pin_cx - r, cy - r, pin_cx + r, cy + r),
        fill=red,
        outline=brown,
        width=max(1, r // 7),
    )
    # Subtle shading lower-right (under highlights)
    draw.ellipse(
        (pin_cx + int(r * 0.12), cy + int(r * 0.08), pin_cx + int(r * 0.92), cy + int(r * 0.88)),
        fill=red_lo,
    )
    # Soft highlight blob upper-left inside head
    draw.ellipse(
        (
            pin_cx - int(r * 0.92),
            cy - int(r * 0.95),
            pin_cx - int(r * 0.12),
            cy - int(r * 0.25),
        ),
        fill=red_hi,
    )
    # Small white specular dot on top
    dot = max(2, r // 6)
    dx = pin_cx - int(r * 0.35)
    dy = cy - int(r * 0.42)
    draw.ellipse((dx - dot // 2, dy - dot // 2, dx + dot // 2, dy + dot // 2), fill=(255, 255, 255, 245))

    y_stem_top = cy + r - 1
    stem_len = max(7, int(r * 0.58))
    y_stem_bot = y_stem_top + stem_len
    # Dark under-stroke so the yellow stick reads on any background
    draw.line((pin_cx, y_stem_top, pin_cx, y_stem_bot), fill=(0, 0, 0, 200), width=stem_w + 3)
    draw.line((pin_cx, y_stem_top, pin_cx, y_stem_bot), fill=stem_yellow, width=stem_w)

    # Flattened elliptical "ground" rings with gaps (broken rings)
    ring_y = y_stem_bot + max(2, stem_w)
    rx_o = int(r * 1.45)
    ry_o = max(3, int(r * 0.26))
    rx_i = int(r * 0.82)
    ry_i = max(2, int(r * 0.16))
    lw = max(2, r // 7)

    bbox_o = (pin_cx - rx_o, ring_y - ry_o, pin_cx + rx_o, ring_y + ry_o)
    bbox_i = (pin_cx - rx_i, ring_y - ry_i, pin_cx + rx_i, ring_y + ry_i)

    for start, end in ((20, 88), (108, 238), (258, 328)):
        draw.arc(bbox_o, start=start, end=end, fill=brown, width=lw)
    for start, end in ((35, 102), (122, 218), (242, 310)):
        draw.arc(bbox_i, start=start, end=end, fill=brown, width=max(1, lw - 1))


def _draw_reel_brand_badge(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    """
    Dead center of frame — drop shadow + white fill + stroke for legibility on any footage.
    """
    text = (getattr(config, "REEL_BRAND_TEXT", "") or "").strip()
    if not text:
        return
    font_size = max(30, min(52, int(h * 0.034)))
    font = _try_overlay_font_stack(_BODY_FONT_STACK, font_size)
    cx = w // 2
    cy = h // 2
    sw = max(2, min(7, int(h * 0.0028)))
    fill = (255, 255, 255, 255)
    stroke_fill = (0, 0, 0, 200)
    off = max(2, min(6, int(h * 0.004)))
    off2 = max(1, off - 1)
    shadow_fill = (0, 0, 0, 185)
    shadow_offsets = (
        (off, off),
        (off2, off2),
        (off, 0),
        (0, off),
        (-off, off),
        (off, -off2),
    )
    try:
        for ox, oy in shadow_offsets:
            draw.text(
                (cx + ox, cy + oy),
                text,
                font=font,
                fill=shadow_fill,
                anchor="mm",
            )
        draw.text(
            (cx, cy),
            text,
            font=font,
            fill=fill,
            stroke_width=sw,
            stroke_fill=stroke_fill,
            anchor="mm",
        )
    except TypeError:
        bb = draw.textbbox((0, 0), text, font=font)
        tx = int(cx - (bb[0] + bb[2]) / 2.0)
        ty = int(cy - (bb[1] + bb[3]) / 2.0)
        for ox, oy in shadow_offsets:
            draw.text(
                (tx + ox, ty + oy),
                text,
                font=font,
                fill=shadow_fill,
            )
        draw.text(
            (tx, ty),
            text,
            font=font,
            fill=fill,
            stroke_width=sw,
            stroke_fill=stroke_fill,
        )


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


def _hook_location_keywords(hint: str) -> set[str]:
    """Lowercase keys from clip title (e.g. \"Porto, Portugal\") to match in hook text."""
    if not (hint or "").strip():
        return set()
    keys: set[str] = set()
    h = hint.split("—")[0].strip()
    for chunk in h.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        keys.add(chunk.lower())
        for w in chunk.split():
            wl = w.strip().lower()
            if len(wl) >= 2:
                keys.add(wl)
    return keys


def _hook_fragment_style(fragment: str, loc_keys: set[str]) -> str:
    """Return style key: normal | location | shout."""
    if not fragment.strip():
        return "normal"
    wstrip = fragment.strip()
    core = wstrip.strip(".,!?;:…\"'").lower()
    if core and core in loc_keys:
        return "location"
    alpha = "".join(c for c in wstrip if c.isalpha())
    if alpha and alpha.isupper() and len(alpha) >= 4:
        return "shout"
    return "normal"


def _split_hook_preserve_space(text: str) -> list[str]:
    parts = re.findall(r"\S+|\s+", (text or "").strip())
    return parts if parts else []


def _hook_token_line_width(
    draw: ImageDraw.ImageDraw,
    line: list[tuple[str, str]],
    fonts: Mapping[str, ImageFont.ImageFont],
) -> int:
    wsum = 0
    for frag, sty in line:
        f = fonts[sty]
        bb = draw.textbbox((0, 0), frag, font=f)
        wsum += bb[2] - bb[0]
    return wsum


def _wrap_hook_lines(
    draw: ImageDraw.ImageDraw,
    fragments: list[str],
    loc_keys: set[str],
    fonts: Mapping[str, ImageFont.ImageFont],
    max_w: int,
    max_lines: int,
) -> list[list[tuple[str, str]]]:
    typed: list[tuple[str, str]] = [(f, _hook_fragment_style(f, loc_keys)) for f in fragments]
    lines: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    cur_w = 0

    def flush() -> None:
        nonlocal cur, cur_w
        if cur:
            lines.append(cur)
        cur = []
        cur_w = 0

    for frag, sty in typed:
        is_space_only = frag.isspace()
        f = fonts[sty]
        bb = draw.textbbox((0, 0), frag, font=f)
        frag_w = bb[2] - bb[0]
        if is_space_only and not cur:
            continue
        if cur_w + frag_w <= max_w or not cur:
            cur.append((frag, sty))
            cur_w += frag_w
            continue
        if cur:
            flush()
            if is_space_only:
                continue
            cur.append((frag, sty))
            cur_w = frag_w
        else:
            cur.append((frag, sty))
            cur_w = frag_w
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines]


def _try_hook_font(size: int) -> ImageFont.ImageFont:
    """Bold display font for hook (matches title weight)."""
    return _try_overlay_font_stack(_TITLE_FONT_STACK, size, system_bold_fallback=True)


def _draw_text_with_drop_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    stroke_w: int,
) -> None:
    x, y = xy
    for dx, dy in ((4, 4), (2, 2)):
        draw.text(
            (x + dx, y + dy),
            text,
            font=font,
            fill=_HOOK_SHADOW_RGBA,
            stroke_width=stroke_w + 1,
            stroke_fill=(0, 0, 0, 200),
        )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_w,
        stroke_fill=(0, 0, 0, 230),
    )


def _render_hook_overlay_png(
    out_png: Path,
    hook_text: str,
    location_hint: str,
    *,
    anchor_x: float,
    anchor_y: float,
    font_scale: float,
    frame_w: int,
    frame_h: int,
) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    loc_keys = _hook_location_keywords(location_hint)
    fs = max(0.6, min(1.7, float(font_scale)))
    max_w = int(frame_w * 0.88)
    line_gap = max(6, int(frame_h * 0.006))

    sz_n = int(frame_h * 0.032 * fs)
    sz_loc = int(frame_h * 0.052 * fs)
    sz_sh = int(frame_h * 0.046 * fs)
    fonts = {
        "normal": _try_hook_font(sz_n),
        "location": _try_hook_font(sz_loc),
        "shout": _try_hook_font(sz_sh),
    }

    frags = _split_hook_preserve_space(hook_text)
    if not frags:
        _draw_reel_brand_badge(draw, frame_w, frame_h)
        img.save(out_png)
        return out_png

    lines = _wrap_hook_lines(draw, frags, loc_keys, fonts, max_w, 6)
    line_metrics: list[tuple[int, int]] = []
    for line in lines:
        asc = 0
        desc = 0
        for frag, sty in line:
            f = fonts[sty]
            bb = draw.textbbox((0, 0), frag, font=f)
            asc = max(asc, -bb[1])
            desc = max(desc, bb[3])
        line_metrics.append((asc, desc))

    block_h = sum(a + d for a, d in line_metrics) + max(0, len(lines) - 1) * line_gap
    cx = float(anchor_x) * float(frame_w)
    cy = float(anchor_y) * float(frame_h)
    y_cursor = cy - block_h / 2.0

    stroke_n = max(1, int(round(fs * 0.9)))
    stroke_loc = max(2, int(round(fs * 1.25)))
    stroke_sh = max(2, int(round(fs * 1.15)))

    for li, line in enumerate(lines):
        line_asc, line_desc = line_metrics[li]
        total_w = _hook_token_line_width(draw, line, fonts)
        x = cx - total_w / 2.0
        baseline_y = y_cursor + line_asc
        for frag, sty in line:
            f = fonts[sty]
            sw = stroke_loc if sty == "location" else (stroke_sh if sty == "shout" else stroke_n)
            fill = _HOOK_FILL_YELLOW if sty == "location" else _HOOK_FILL_WHITE
            bb0 = draw.textbbox((0, 0), frag, font=f)
            y_draw = int(round(baseline_y + bb0[1]))
            xd = int(round(x))
            _draw_text_with_drop_shadow(draw, (xd, y_draw), frag, f, fill, stroke_w=sw)
            bb = draw.textbbox((xd, y_draw), frag, font=f)
            frag_w = bb[2] - bb[0]
            if sty == "shout" and frag.strip() and not frag.isspace():
                und_y = bb[3] + max(2, int(frame_h * 0.004))
                und_h = max(3, int(frame_h * 0.005 * fs))
                und_x0 = bb[0] - 2
                und_x1 = bb[2] + 2
                draw.rounded_rectangle(
                    (und_x0, und_y, und_x1, und_y + und_h),
                    radius=max(2, und_h // 2),
                    fill=_HOOK_FILL_YELLOW,
                )
            x += frag_w
        y_cursor += line_asc + line_desc + line_gap

    _draw_reel_brand_badge(draw, frame_w, frame_h)
    img.save(out_png)
    return out_png


def _render_caption_overlay(
    out_png: Path,
    caption: str,
    *,
    title: str = "",
    caption_text: str = "",
    anchor_x: float = 0.5,
    anchor_y: float = 0.5,
    font_scale: float = 1.0,
    hook_mode: bool = False,
    hook_location_hint: str = "",
) -> Path:
    w, h = config.REEL_SIZE
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    title_t = (title or "").strip()
    body_t = (caption or "").strip()
    sub_t = (caption_text or "").strip()

    anchor_x = max(0.0, min(1.0, float(anchor_x)))
    anchor_y = max(0.0, min(1.0, float(anchor_y)))
    font_scale = max(0.6, min(1.7, float(font_scale)))

    max_w = int(w * 0.84)
    line_gap = max(8, int(h * 0.008))
    # Tighter leading for caption_text (place blurb) under the title
    sub_line_gap = max(3, int(h * 0.0042))
    title_body_gap = max(10, int(h * 0.014))
    title_sub_gap = max(8, int(h * 0.011))
    pad_x = int(max(30, w * 0.06))
    pad_y = int(max(20, h * 0.022))

    if not title_t and not body_t and not sub_t:
        _draw_reel_brand_badge(draw, w, h)
        img.save(out_png)
        return out_png

    if hook_mode and body_t:
        return _render_hook_overlay_png(
            out_png,
            body_t,
            hook_location_hint,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            font_scale=font_scale,
            frame_w=w,
            frame_h=h,
        )

    # No location title: centered block(s) — optional place blurb + body
    if not title_t:
        sub_font = _try_overlay_font_stack(
            _BODY_FONT_STACK,
            int(h * 0.0265 * font_scale),
        )
        body_font = _try_overlay_font_stack(
            _BODY_FONT_STACK,
            int(h * 0.030 * font_scale),
        )
        sub_lines = _wrap_words_to_lines(draw, sub_t, sub_font, max_w, 4) if sub_t else []
        body_lines = _wrap_words_to_lines(draw, body_t, body_font, max_w, 5) if body_t else []
        if not sub_lines and not body_lines:
            _draw_reel_brand_badge(draw, w, h)
            img.save(out_png)
            return out_png

        sh = [
            draw.textbbox((0, 0), ln, font=sub_font)[3] - draw.textbbox((0, 0), ln, font=sub_font)[1]
            for ln in sub_lines
        ]
        bh0 = [
            draw.textbbox((0, 0), ln, font=body_font)[3] - draw.textbbox((0, 0), ln, font=body_font)[1]
            for ln in body_lines
        ]
        block_h = sum(sh) + max(0, len(sub_lines) - 1) * sub_line_gap
        if body_lines:
            block_h += (title_sub_gap if sub_lines else 0) + sum(bh0) + max(0, len(body_lines) - 1) * line_gap
        block_w = 0
        for ln in sub_lines:
            bb = draw.textbbox((0, 0), ln, font=sub_font)
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
        if CAPTION_OVERLAY_PANEL_RGBA is not None and sub_t:
            draw.rounded_rectangle(
                (x0, y0, x0 + rect_w, y0 + rect_h),
                radius=int(min(34, h * 0.03)),
                fill=CAPTION_OVERLAY_PANEL_RGBA,
            )
        cy_line = y0 + pad_y
        sub_stroke_strong = max(2, int(round(font_scale * 1.12)))
        body_stroke0 = max(1, int(round(font_scale * 0.85)))
        for ln in sub_lines:
            bb = draw.textbbox((0, 0), ln, font=sub_font)
            tw = bb[2] - bb[0]
            tx = int(round((x0 + rect_w / 2.0) - tw / 2.0))
            _draw_text_with_drop_shadow(
                draw,
                (tx, cy_line),
                ln,
                sub_font,
                (248, 250, 255, 255),
                stroke_w=sub_stroke_strong,
            )
            cy_line += (bb[3] - bb[1]) + sub_line_gap
        if body_lines:
            if sub_lines:
                cy_line += title_sub_gap - sub_line_gap
            for ln in body_lines:
                bb = draw.textbbox((0, 0), ln, font=body_font)
                tw = bb[2] - bb[0]
                tx = int(round((x0 + rect_w / 2.0) - tw / 2.0))
                draw.text(
                    (tx, cy_line),
                    ln,
                    font=body_font,
                    fill=(230, 235, 245, 252),
                    stroke_width=body_stroke0,
                    stroke_fill=(0, 0, 0, 150),
                )
                cy_line += (bb[3] - bb[1]) + line_gap
        _draw_reel_brand_badge(draw, w, h)
        img.save(out_png)
        return out_png

    title_font = _try_overlay_font_stack(
        _TITLE_FONT_STACK,
        int(h * 0.040 * font_scale),
        system_bold_fallback=True,
    )
    subtitle_font = _try_overlay_font_stack(
        _BODY_FONT_STACK,
        int(h * 0.0265 * font_scale),
    )
    body_font = _try_overlay_font_stack(
        _BODY_FONT_STACK,
        int(h * 0.030 * font_scale),
    )
    # Pin column width from title size; tight gap so the pin sits close to the location name.
    title_px = int(h * 0.040 * font_scale)
    r_pin = max(7, min(17, int(title_px * 0.82)))
    rx_o_pin = int(r_pin * 1.45)
    pin_col_w = 2 * rx_o_pin + 8
    pin_gap = 5
    col_w = max(120, max_w - pin_col_w - pin_gap)
    title_lines = _wrap_words_to_lines(draw, title_t, title_font, col_w, 2)
    sub_lines = _wrap_words_to_lines(draw, sub_t, subtitle_font, col_w, 4) if sub_t else []
    body_lines = _wrap_words_to_lines(draw, body_t, body_font, col_w, 5) if body_t else []

    th = [
        draw.textbbox((0, 0), ln, font=title_font)[3]
        - draw.textbbox((0, 0), ln, font=title_font)[1]
        for ln in title_lines
    ]
    sh = [
        draw.textbbox((0, 0), ln, font=subtitle_font)[3]
        - draw.textbbox((0, 0), ln, font=subtitle_font)[1]
        for ln in sub_lines
    ]
    bh = [
        draw.textbbox((0, 0), ln, font=body_font)[3] - draw.textbbox((0, 0), ln, font=body_font)[1]
        for ln in body_lines
    ]
    block_h = sum(th) + max(0, len(title_lines) - 1) * line_gap
    if sub_lines:
        block_h += title_sub_gap + sum(sh) + max(0, len(sub_lines) - 1) * sub_line_gap
    if body_lines:
        block_h += title_body_gap + sum(bh) + max(0, len(body_lines) - 1) * line_gap

    max_title_tw = 0
    for ln in title_lines:
        bb = draw.textbbox((0, 0), ln, font=title_font)
        max_title_tw = max(max_title_tw, bb[2] - bb[0])
    max_sub_tw = 0
    for ln in sub_lines:
        bb = draw.textbbox((0, 0), ln, font=subtitle_font)
        max_sub_tw = max(max_sub_tw, bb[2] - bb[0])
    max_body_tw = 0
    for ln in body_lines:
        bb = draw.textbbox((0, 0), ln, font=body_font)
        max_body_tw = max(max_body_tw, bb[2] - bb[0])
    text_block_w = max(max_title_tw, max_sub_tw, max_body_tw)
    block_w = pin_col_w + pin_gap + text_block_w if title_lines else text_block_w

    rect_w = min(w - 36, block_w + pad_x * 2)
    rect_h = block_h + pad_y * 2
    cx = float(anchor_x) * float(w)
    cy = float(anchor_y) * float(h)
    x0 = int(round(cx - rect_w / 2.0))
    y0 = int(round(cy - rect_h / 2.0))
    x0 = max(10, min(x0, w - rect_w - 10))
    y0 = max(10, min(y0, h - rect_h - 10))

    if CAPTION_OVERLAY_PANEL_RGBA is not None and sub_lines:
        draw.rounded_rectangle(
            (x0, y0, x0 + rect_w, y0 + rect_h),
            radius=int(min(34, h * 0.03)),
            fill=CAPTION_OVERLAY_PANEL_RGBA,
        )

    cy_line = y0 + pad_y
    inner_left = x0 + pad_x
    text_x0 = inner_left + pin_col_w + pin_gap
    pin_cx_title = inner_left + rx_o_pin + 3
    title_stroke = max(1, int(round(font_scale * 1.05)))
    sub_stroke_strong = max(2, int(round(font_scale * 1.12)))
    body_stroke = max(1, int(round(font_scale * 0.85)))
    for ti, ln in enumerate(title_lines):
        bb = draw.textbbox((0, 0), ln, font=title_font)
        line_h = bb[3] - bb[1]
        if ti == 0:
            line_center_y = cy_line + line_h // 2
            _draw_map_pin(draw, pin_cx_title, line_center_y, line_h, frame_h=h)
        tx = text_x0
        draw.text(
            (tx, cy_line),
            ln,
            font=title_font,
            fill=(255, 255, 255, 255),
            stroke_width=title_stroke,
            stroke_fill=(0, 0, 0, 170),
        )
        cy_line += line_h + line_gap

    if sub_lines:
        cy_line += title_sub_gap - line_gap
        for ln in sub_lines:
            bb = draw.textbbox((0, 0), ln, font=subtitle_font)
            tx = text_x0
            _draw_text_with_drop_shadow(
                draw,
                (tx, cy_line),
                ln,
                subtitle_font,
                (248, 250, 255, 255),
                stroke_w=sub_stroke_strong,
            )
            cy_line += (bb[3] - bb[1]) + sub_line_gap

    if body_lines:
        gap_trim = sub_line_gap if sub_lines else line_gap
        cy_line += title_body_gap - gap_trim

    for ln in body_lines:
        bb = draw.textbbox((0, 0), ln, font=body_font)
        tx = text_x0
        draw.text(
            (tx, cy_line),
            ln,
            font=body_font,
            fill=(230, 235, 245, 252),
            stroke_width=body_stroke,
            stroke_fill=(0, 0, 0, 150),
        )
        cy_line += (bb[3] - bb[1]) + line_gap

    _draw_reel_brand_badge(draw, w, h)
    img.save(out_png)
    return out_png


def _vf_cover_overlay_chain(w: int, h: int) -> str:
    """
    Scale and center-crop to fill the reel frame (same idea as CSS ``object-fit: cover``),
    then draw the caption overlay.
    """
    return (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/2,setsar=1,format=yuv420p,fps=30[v];"
        f"[1:v]format=rgba[ov];[v][ov]overlay=0:0:format=auto"
    )


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
        _vf_cover_overlay_chain(w, h),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-t",
        f"{seconds:.3f}",
        str(out_mp4),
    ]
    media_processor._run_ffmpeg_cmd(cmd, "manual reel image segment")  # type: ignore[attr-defined]


def _make_segment_from_video(src: Path, overlay_png: Path, out_mp4: Path, seconds: float) -> None:
    w, h = config.REEL_SIZE
    exe = media_processor._ensure_ffmpeg()  # type: ignore[attr-defined]
    # Loop so short clips fill the full segment duration; -t trims to match image segments for concat.
    cmd = [
        exe,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(src),
        "-i",
        str(overlay_png),
        "-filter_complex",
        _vf_cover_overlay_chain(w, h),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-t",
        f"{seconds:.3f}",
        str(out_mp4),
    ]
    media_processor._run_ffmpeg_cmd(cmd, "manual reel video segment")  # type: ignore[attr-defined]


def _concat_video_parts(parts: Sequence[Path], out_mp4: Path) -> None:
    """Lossless chain of same-codec vertical clips (no audio)."""
    paths = [Path(p) for p in parts if Path(p).is_file()]
    if not paths:
        raise RuntimeError("No parts to concat.")
    if len(paths) == 1:
        shutil.copy(paths[0], out_mp4)
        return
    exe = media_processor._ensure_ffmpeg()  # type: ignore[attr-defined]
    cmd: list[str] = [exe, "-y"]
    for p in paths:
        cmd.extend(["-i", str(p)])
    labels = "".join(f"[{i}:v]" for i in range(len(paths)))
    filt = f"{labels}concat=n={len(paths)}:v=1:a=0[v]"
    cmd.extend(
        [
            "-filter_complex",
            filt,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            str(out_mp4),
        ]
    )
    media_processor._run_ffmpeg_cmd(cmd, "manual reel concat segment parts")  # type: ignore[attr-defined]


def build_manual_reel(
    *,
    uploads_dir: Path,
    media_paths: list[Path],
    captions: list[str],
    music_track_id: str | None,
    transition_type: str = "slideleft",
    transition_speed: str = "auto",
    transition_xfade_scale: float | None = None,
    overlay_positions: list[tuple[float, float]] | None = None,
    overlay_font_scales: list[float] | None = None,
    titles: list[str] | None = None,
    caption_texts: list[str] | None = None,
    hook_caption: str = "",
    hook_seconds: float = 3.0,
    image_segment_seconds: float = 3.0,
    video_segment_seconds: float = 5.0,
) -> dict[str, Any]:
    if not media_paths:
        raise RuntimeError("Upload at least one image or video.")
    if len(captions) < len(media_paths):
        captions = captions + [""] * (len(media_paths) - len(captions))
    tit_list = list(titles) if titles else []
    if len(tit_list) < len(media_paths):
        tit_list = tit_list + [""] * (len(media_paths) - len(tit_list))
    sub_list = list(caption_texts) if caption_texts else []
    if len(sub_list) < len(media_paths):
        sub_list = sub_list + [""] * (len(media_paths) - len(sub_list))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_manual_" + _slug(media_paths[0].stem)
    out_dir = config.OUTPUT_DIR / "manual_reels" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    reel_work = out_dir / "work"
    reel_work.mkdir(parents=True, exist_ok=True)

    n = len(media_paths)
    fps = 30
    img_sec = max(0.5, min(90.0, float(image_segment_seconds)))
    vid_sec = max(0.5, min(90.0, float(video_segment_seconds)))

    seg_list: list[float] = []
    for src in media_paths:
        raw = vid_sec if _is_video(src) else img_sec
        seg_list.append(max(1.0 / fps, int(round(raw * fps)) / float(fps)))

    total_clips = sum(seg_list)
    min_seg = min(seg_list) if seg_list else img_sec
    xfade = 0.0
    out_duration = total_clips

    # Transition controls are implemented at the concat stage.
    # For "none" we use hard cuts (concat filter) instead of xfade.
    none_mode = transition_type == "none"
    if n > 1 and not none_mode:
        base_xfade = media_processor._reel_pick_xfade_seconds(min_seg)  # type: ignore[attr-defined]
        if transition_xfade_scale is not None:
            speed_factor = float(transition_xfade_scale)
        else:
            speed = (transition_speed or "auto").lower().strip()
            speed_factor = 1.0
            # Higher factor = longer, more visible xfade ("slower" transition).
            if speed == "slow":
                speed_factor = 1.22
            elif speed == "slower":
                speed_factor = 1.38
            elif speed == "slowest":
                speed_factor = 1.55
            elif speed in {"fast", "faster"}:
                speed_factor = 0.78
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

        # Avoid xfade durations that consume the shortest clip too aggressively.
        if xfade >= min_seg - 0.06:
            xfade = max(lo * 0.82, min(hi * 0.88, min_seg * 0.38))

        out_duration = total_clips - max(0, n - 1) * xfade

    seg_paths: list[Path] = []

    if overlay_positions is None:
        overlay_positions = [DEFAULT_OVERLAY_ANCHOR] * n
    if overlay_font_scales is None:
        overlay_font_scales = [1.0] * n
    if len(overlay_positions) < n:
        overlay_positions = list(overlay_positions) + [DEFAULT_OVERLAY_ANCHOR] * (n - len(overlay_positions))
    if len(overlay_font_scales) < n:
        overlay_font_scales = list(overlay_font_scales) + [1.0] * (n - len(overlay_font_scales))

    hook_raw = (hook_caption or "").strip()
    hook_dur_req = max(0.0, float(hook_seconds))

    for i, src in enumerate(media_paths):
        seg = seg_list[i]
        ov = reel_work / f"overlay_{i:02d}.png"
        anchor = overlay_positions[i] if i < len(overlay_positions) else DEFAULT_OVERLAY_ANCHOR
        fs = overlay_font_scales[i] if i < len(overlay_font_scales) else 1.0
        cap_i = captions[i] if i < len(captions) else ""
        tit_i = (tit_list[i] if i < len(tit_list) else "").strip()
        sub_i = (sub_list[i] if i < len(sub_list) else "").strip()
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
            caption_text=sub_i,
            anchor_x=anchor[0],
            anchor_y=anchor[1],
            font_scale=fs,
        )
        segp = reel_work / f"seg_{i:02d}.mp4"

        use_hook = i == 0 and bool(hook_raw) and hook_dur_req > 0
        if use_hook:
            frames_total = max(1, int(round(seg * fps)))
            frames_hook = min(
                int(round(min(hook_dur_req, seg) * fps)),
                frames_total,
            )
            frames_rest = frames_total - frames_hook
            t_hook = frames_hook / float(fps)
            t_rest = frames_rest / float(fps)
            parts: list[Path] = []
            if frames_hook > 0:
                ov_hook = reel_work / f"overlay_{i:02d}_hook.png"
                _render_caption_overlay(
                    ov_hook,
                    hook_raw,
                    title="",
                    caption_text="",
                    anchor_x=anchor[0],
                    anchor_y=anchor[1],
                    font_scale=fs,
                    hook_mode=True,
                    hook_location_hint=tit_i,
                )
                hook_part = reel_work / f"seg_{i:02d}_hook.mp4"
                if _is_video(src):
                    _make_segment_from_video(src, ov_hook, hook_part, t_hook)
                else:
                    _make_segment_from_image(src, ov_hook, hook_part, t_hook)
                parts.append(hook_part)
            if frames_rest > 0:
                rest_part = reel_work / f"seg_{i:02d}_rest.mp4"
                if _is_video(src):
                    _make_segment_from_video(src, ov, rest_part, t_rest)
                else:
                    _make_segment_from_image(src, ov, rest_part, t_rest)
                parts.append(rest_part)
            if not parts:
                if _is_video(src):
                    _make_segment_from_video(src, ov, segp, seg)
                else:
                    _make_segment_from_image(src, ov, segp, seg)
            elif len(parts) == 1:
                shutil.copy(parts[0], segp)
            else:
                _concat_video_parts(parts, segp)
        else:
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
                "18",
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
            seg_list[0],
            xfade,
            out_duration,
            no_audio,
            context="manual reel xfade concat",
            transition_style=transition_style,
            segment_durations=seg_list,
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
        "image_segment_seconds": img_sec,
        "video_segment_seconds": vid_sec,
        "segment_durations_seconds": seg_list,
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

