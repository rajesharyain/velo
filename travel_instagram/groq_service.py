"""
Groq LLM integration: tourism guide JSON with scenery categories and Pexels-oriented search strings.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from groq import Groq

from travel_instagram import config

logger = logging.getLogger(__name__)

def infer_requested_destination_count(theme: str) -> int | None:
    """
    Parse phrases like "top 10 places in Portugal" or "5 places to visit in Madrid".

    Returns a positive int capped at DESTINATION_REQUEST_MAX, or None if no count found.
    """
    t = (theme or "").strip().lower()
    patterns = (
        r"\btop\s+(\d+)\b",
        r"\bbest\s+(\d+)\s+places\b",
        r"\b(\d+)\s+best\s+places\b",
        r"\b(\d+)\s+places\s+to\s+visit\b",
        r"\b(\d+)\s+places\s+in\b",
        r"\bmust[\s-]see\s+(\d+)\b",
    )
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            n = int(m.group(1))
            if n < 1:
                continue
            return min(n, config.DESTINATION_REQUEST_MAX)
    return None


def infer_geo_focus_from_theme(theme: str) -> str | None:
    """
    Extract geographic scope from phrases like "places to visit in Portugal",
    "top 5 in Madrid", "beaches in the Algarve".

    Used to force "Place, Country/City" labels and to enrich short Groq names
    (e.g. "Lisbon" → "Lisbon, Portugal") when the theme names the parent region.
    """
    t = (theme or "").strip()
    patterns = (
        r"\bplaces\s+to\s+visit\s+in\s+([A-Za-z][A-Za-z\s'-]{1,56})",
        r"\bplaces\s+in\s+([A-Za-z][A-Za-z\s'-]{1,56})(?:\s*$|\s*[,.]|\s+(?:to|for|and)\b)",
        r"\bvisit\s+in\s+([A-Za-z][A-Za-z\s'-]{1,56})\s*$",
        r"\b(?:around|across|throughout)\s+([A-Za-z][A-Za-z\s'-]{1,56})\s*$",
        r"\btop\s+\d+\s+(?:places\s+)?(?:to\s+visit\s+)?in\s+([A-Za-z][A-Za-z\s'-]{1,56})",
        r"\bin\s+([A-Za-z][A-Za-z\s'-]{1,56})\s*$",
    )
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            s = m.group(1).strip().rstrip(".,;")
            if s.lower().startswith("the "):
                s = s[4:].strip()
            if 2 <= len(s) <= 56:
                return s
    return None


def enrich_destination_label(destination: str, geo: str | None) -> str:
    """
    Append geographic scope when the model returned a short name only
    (e.g. theme says 'in Portugal' but row is just 'Lisbon').
    """
    d = (destination or "").strip()
    if not d or not geo:
        return d
    g = geo.strip()
    if not g:
        return d
    if g.lower() in d.lower():
        return d
    if " — " in d:
        left, _, right = d.partition(" — ")
        left = left.strip()
        right = right.strip()
        if "," not in left and g.lower() not in left.lower():
            left = f"{left}, {g}"
        return f"{left} — {right}" if right else left
    if "," not in d:
        return f"{d}, {g}"
    return d


def _travel_system_prompt(dest_min: int, dest_max: int) -> str:
    if dest_min == dest_max:
        count_block = (
            f"- Output EXACTLY {dest_max} destination rows — no more, no fewer. "
            'For "top N places in [country/region/city]" style prompts, each row is one distinct ranked place, neighborhood, or must-see stop; '
            "no duplicate or near-duplicate locations. Every pexels_search_query must name that specific spot plus country/region (English stock-search terms)."
        )
        single_place = (
            "When the brief names ONE primary city/region and lists multiple shots OR implies N distinct stops:\n"
            f"- Emit exactly {dest_max} rows total — one per scene or sub-place. If the user listed fewer than {dest_max} scenes, invent complementary distinct scenes or sub-areas for the same area until you reach {dest_max} rows.\n"
            '- "destination" should be "City, Country — short scene label" when multiple rows share one city.\n'
            "- pexels_search_query MUST include the real searchable place (city + country when known) PLUS concrete visuals for THAT row only. 6 to 16 words. No hashtags."
        )
        multi_place = (
            f"When the brief is a ranked list or multi-place theme without a per-scene list, output exactly {dest_max} different places or major stops. "
            "Each pexels_search_query combines that place + a DISTINCT attraction angle (monument, beach, nightlife, culture, activity, viewpoint, etc.)."
        )
    else:
        count_block = (
            f"- Output between {dest_min} and {dest_max} destination rows (inclusive). "
            "Never exceed this range."
        )
        single_place = (
            "When the brief names ONE primary place and lists MULTIPLE requested shots/scenes:\n"
            f"- Emit one row per requested scene (up to {dest_max}). If fewer than {dest_min} scenes are listed, add complementary distinct scenes for the same place until you have at least {dest_min} rows.\n"
            '- "destination" should be "City, Country — short scene label" (e.g. "Lisbon, Portugal — Tram 28") so rows stay unique.\n'
            "- pexels_search_query MUST include the real searchable place (city + country when known) PLUS concrete visuals for THAT scene only. 6 to 16 words. No hashtags."
        )
        multi_place = (
            f"When the brief is a broad multi-place theme (no explicit shot list), choose {dest_min} to {dest_max} different cities/regions; "
            "each pexels_search_query combines place + a DISTINCT attraction angle (monument, beach, nightlife, culture, activity, etc.)."
        )

    return f"""You are a travel and tourism destination guide expert. You output ONLY valid JSON, no markdown.

Audience: Instagram travel guides for content creators — prioritize what travelers actually seek: historic monuments and landmarks, beaches and coasts, nightlife (bars, clubs, rooftop views, neon streets), things to do (activities, tours, markets, promenades), local culture (museums, festivals, traditional quarters, food scenes), and other iconic attractions. Stock media on Pexels should read as recognizable travel B-roll, not generic "travel" filler.

The user message may be a SHORT theme (e.g. "coastal Portugal") OR a LONGER media brief that names place(s) and asks for specific shots/scenes (e.g. "Lisbon — fetch images/video for yellow trams, Alfama alleys, sunset at Miradouro"). Treat instructions like "fetch", "get", "find", "Pexels", "stock" as: you must produce search queries that will retrieve matching stock media.

Rules:
{count_block}
- Each row must be UNIQUE: use distinct "destination" labels and distinct "pexels_search_query" strings (no near-duplicate queries).

{single_place}

{multi_place}

Destination labeling (critical for Pexels and on-video titles):
- The "destination" field must make the real place unambiguous. For country/region roundups (e.g. "top N in Portugal"), use "City or region, Country" on every row: "Lisbon, Portugal", "Porto, Portugal", "Algarve, Portugal" — never a lone bare city name when the country is obvious from the user's theme.
- For city-scoped lists (e.g. "top spots in Madrid"), use "Landmark or neighborhood, Madrid" (or the city name the user gave).
- If you add a scene suffix, use an em dash: "Lisbon, Portugal — Yellow trams".

When the user does not spell out scenes, bias rows toward a MIX of attraction types where it fits the theme: e.g. one historic monument or landmark, one beach/coast/waterfront if relevant, one nightlife or golden-hour city energy, one culture or museum/market/traditional quarter, one scenic viewpoint or "things to do" outdoor activity.

For EACH destination you MUST:
  - Classify using scape_types: pick 2 to 5 tags from this vocabulary (underscore form; use exact wording where possible). Include tags that match the row's attraction type (monuments, beach, nightlife, culture, activities):
    beach, coastline, ocean, waterfront, harbor, boardwalk, tropical, island, mountains, alpine, volcano, forest, jungle, lake, river, waterfall, desert, canyon, countryside, vineyard, rice_terraces, savanna, glacier, city, skyline, night_skyline, historic_town, old_town, architecture, street_cafe, night_market, nightlife, rooftop_bar, temple, castle, palace, cathedral, historic_monument, ruins, museum, gallery, landmark, scenic_view, hiking_trail, road_trip, food_market, street_food, festival, parade, local_culture, winter_snow, spring_bloom, sunset_view
  - pexels_search_query: must reflect that row's attraction type with concrete English stock-search terms (e.g. monument facade columns, sandy beach turquoise water, city street neon night bar, crowded food market stalls, museum gallery visitors, traditional alley lanterns). Still include place name + country when known. 6 to 16 words. No hashtags.
  - vibe: one short evocative phrase (max 18 words) capturing the sensory mood — sounds, smells, colours, feelings. Example: "golden hour mist over ancient rooftops, lanterns glowing in quiet alleyways".
  - caption_text: ON-VIDEO overlay under the location title (Instagram Reels). MAXIMUM 12–15 words (strict). Write as a travel creator sharing a cinematic personal moment — evoke a feeling, sensory detail, or surprising fact that stops the scroll. Examples: "Where time stands still and every corner tells a story", "The one place here you cannot afford to miss". No hashtags. Must NOT copy "caption" verbatim.
  - caption: 22 to 40 words. Write as a travel creator’s personal recommendation for THIS specific location. Answer WHY someone should visit and WHAT they should experience — mention a specific activity, food, viewpoint, or local detail that makes this place unforgettable. Feel like a trusted friend’s advice, not a guidebook entry. MUST name the city and country from this row’s "destination". No hashtags.
- hook: max 8 words, title for slide 1 (can nod to the main place or theme).
- cta: max 10 words, final slide.
- hashtags: 8 to 12 tags WITHOUT the # symbol in JSON.
- Escape quotes in strings. ASCII-friendly punctuation."""


USER_PROMPT_TEMPLATE = """Theme or media brief (may include place + requested shots, or a broad multi-destination theme):

{theme}

{geo_line}{count_line}

Return this JSON shape (all fields required on each destination object):
{{
  "hook": "string",
  "cta": "string",
  "hashtags": ["travel", "wanderlust"],
  "destinations": [
    {{
      "destination": "City, Country — scene label OR City, Country for broad themes",
      "caption_text": "12–15 words max; opens with a hook; scroll-stopping, simple, emotional",
      "caption": "18–38 words; location + experience; line below the blurb",
      "scape_types": ["historic_monument", "city", "landmark"],
      "vibe": "short mood and atmosphere phrase",
      "pexels_search_query": "place + distinct scene keywords for Pexels image/video search"
    }}
  ]
}}"""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def _norm_scape_types(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        s = str(x).strip().lower().replace(" ", "_")
        if s and s not in out:
            out.append(s)
    return out[:8]


def _fallback_pexels_query(name: str, scape_types: list[str], vibe: str) -> str:
    parts = [name] + scape_types[:4]
    v = " ".join(vibe.split())[:80]
    if v:
        parts.append(v)
    return " ".join(parts)[:200].strip()


def _base_location_label(destination: str) -> str:
    """City/region + country part before an em-dash scene suffix, e.g. 'Lisbon, Portugal'."""
    s = (destination or "").strip()
    if " — " in s:
        return s.split(" — ", 1)[0].strip()
    return s


def base_location_label(destination: str) -> str:
    """Public helper: on-image title for a Groq destination row (place without scene suffix)."""
    return _base_location_label(destination)


def normalize_reel_caption_text(text: str, *, max_words: int = 15) -> str:
    """Clamp on-reel blurb length (Instagram Reels caption_text)."""
    words = (text or "").strip().split()
    if not words:
        return ""
    return " ".join(words[: max(1, max_words)])


def _ensure_caption_includes_location(caption: str, destination: str) -> str:
    """Prefix caption with the row's location when Groq omitted it (Pexels/download alignment)."""
    cap = (caption or "").strip()
    base = _base_location_label(destination) or (destination or "").strip()
    if not base:
        return cap
    if base.lower() in cap.lower():
        return cap if cap else base
    if not cap:
        return base
    return f"{base}. {cap}"


def _dedupe_destinations(
    items: list[dict[str, Any]],
    geo_hint: str | None = None,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in items:
        name = str(row.get("destination", "")).strip()
        if name and geo_hint:
            name = enrich_destination_label(name, geo_hint)
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        cap = str(row.get("caption", "")).strip()
        cap = _ensure_caption_includes_location(cap, name)
        words = cap.split()
        if len(words) > 48:
            cap = " ".join(words[:48])
        ctext = normalize_reel_caption_text(str(row.get("caption_text") or "").strip(), max_words=15)
        scape = _norm_scape_types(row.get("scape_types"))
        vibe = str(row.get("vibe", "")).strip()[:220]
        pq = str(row.get("pexels_search_query", "")).strip()[:300]
        if not pq:
            pq = _fallback_pexels_query(name, scape, vibe)
        out.append(
            {
                "destination": name,
                "caption": cap,
                "caption_text": ctext,
                "scape_types": scape,
                "vibe": vibe,
                "pexels_search_query": pq,
            }
        )
    return out


def _validate_and_trim(
    data: dict[str, Any],
    theme: str,
    *,
    dest_min: int,
    dest_max: int,
    geo_hint: str | None = None,
) -> dict[str, Any]:
    destinations = data.get("destinations") or []
    if not isinstance(destinations, list):
        destinations = []

    fixed = _dedupe_destinations(
        [d for d in destinations if isinstance(d, dict)],
        geo_hint=geo_hint,
    )

    if len(fixed) > dest_max:
        fixed = fixed[:dest_max]
    if len(fixed) < dest_min:
        logger.warning(
            "Groq returned fewer than %s destinations (wanted %s–%s); proceeding with %s",
            dest_min,
            dest_min,
            dest_max,
            len(fixed),
        )

    hook = str(data.get("hook") or f"Best {theme} spots").strip()
    cta = str(data.get("cta") or "Follow for more travel ideas").strip()
    tags = data.get("hashtags") or []
    if not isinstance(tags, list):
        tags = []
    hashtags = [str(t).lstrip("#").strip() for t in tags if str(t).strip()][:15]

    return {
        "theme": theme,
        "hook": hook[:120],
        "cta": cta[:120],
        "hashtags": hashtags,
        "destinations": fixed,
    }


def generate_travel_content(
    theme: str,
    api_key: str | None = None,
    *,
    destination_count: int | None = None,
) -> dict[str, Any]:
    """
    Call Groq for tourism guide JSON: hook, CTA, hashtags, destinations with
    scape_types, vibe, and pexels_search_query for stock-image/video retrieval.

    ``theme`` may be a short theme or a longer media brief (place + requested shots).

    If ``destination_count`` is None, a count is inferred from the theme (e.g. "top 10 places").
    Otherwise use that exact number (clamped to DESTINATION_REQUEST_MAX).
    """
    key = api_key or config.GROQ_API_KEY
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your environment or .env file."
        )

    theme_s = theme.strip()
    eff_n = destination_count
    if eff_n is None:
        eff_n = infer_requested_destination_count(theme_s)
    if eff_n is not None:
        eff_n = min(max(int(eff_n), 1), config.DESTINATION_REQUEST_MAX)
        dest_min = dest_max = eff_n
        count_line = (
            f"CRITICAL: The destinations array MUST contain exactly {eff_n} objects "
            f"(one per ranked place or scene). Rank them 1–{eff_n} in travel appeal for the user's topic."
        )
    else:
        dest_min, dest_max = config.DESTINATION_COUNT_MIN, config.DESTINATION_COUNT_MAX
        count_line = (
            f"The destinations array MUST contain between {dest_min} and {dest_max} objects (inclusive)."
        )

    system_prompt = _travel_system_prompt(dest_min, dest_max)
    geo = infer_geo_focus_from_theme(theme_s)
    if geo:
        geo_line = (
            f'Geographic scope from the user\'s wording: "{geo}". '
            f'Every "destination" must encode that scope: for a country/region scope use "PlaceName, {geo}" '
            f"(e.g. Lisbon, {geo}; Porto, {geo}; a region like Algarve, {geo}). "
            f'For a city scope, use "Spot, City". Each pexels_search_query must still name the specific place plus concrete visuals.\n\n'
        )
    else:
        geo_line = ""

    user_msg = USER_PROMPT_TEMPLATE.format(
        theme=theme_s,
        geo_line=geo_line,
        count_line=count_line,
    )

    max_tokens = 8192 if dest_max > 10 else 4096 if dest_max > 5 else 2048

    client = Groq(api_key=key)
    completion = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.85,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content
    if not raw:
        raise RuntimeError("Groq returned an empty response.")

    try:
        parsed = _extract_json_object(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Groq JSON: %s\nRaw: %s", e, raw[:500])
        raise RuntimeError("Groq response was not valid JSON.") from e

    out = _validate_and_trim(
        parsed,
        theme_s,
        dest_min=dest_min,
        dest_max=dest_max,
        geo_hint=geo,
    )
    if eff_n is not None:
        out["requested_destination_count"] = eff_n
    if geo:
        out["theme_geo_focus"] = geo
    return out


_REEL_PARSE_SYSTEM_PROMPT = """You are a travel reel prompt refiner.

You must output ONLY valid JSON (no markdown) with this exact schema:
{
  "destination": "string",
  "origin": "string or null",
  "mode": "flight|airport|walking|road|train",
  "image_keywords": "string",
  "video_keywords": "string",
  "refined_prompt": "string"
}

Rules:
- destination: extract the main city/region name from the user prompt.
- origin: extract the "from X" city/country if present, otherwise (also accept "at X") otherwise null.
- mode:
  - airport if the prompt mentions airport or boarding
  - flight if it mentions fly/flying/airplane/plane/clouds (window view is okay)
  - walking if it mentions walk/walking/streets
  - road if it mentions car/road trip
  - train if it mentions train
  If none mentioned, guess based on vibe words; default to flight.
- refined_prompt must be a natural sentence that includes destination, origin (if any),
  and mode keywords so downstream parsing works.

- image_keywords: 3–6 concrete visual keywords for Pexels *images* (landmarks/nature/weather/city scenes)
  that are strongly tied to destination + mode.
- video_keywords: 3–6 concrete visual keywords for Pexels *videos* that describe motion/scene type
  (e.g., "drone coastline", "airport walking luggage", "rainy street walk", "airplane window clouds")

IMPORTANT for keywords:
- Do NOT include specific place names (no city/country names) in image_keywords/video_keywords.
- Keep keywords generic visual concepts only.

IMPORTANT: If origin exists, include the phrase "from <origin>" (do not use "at <origin>").
  If origin is missing, omit "from".

  Example:
  "Beautiful destination Paris, from Faro, now fly through the clouds in vertical reel style."
"""


def parse_reel_prompt(prompt: str, api_key: str | None = None) -> dict[str, Any]:
    """
    Use Groq to refine a natural-language reel prompt into structured fields:
    destination, origin, and mode (+ a refined_prompt string).
    """
    key = api_key or config.GROQ_API_KEY
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set.")

    from groq import Groq  # local import to keep module init light

    client = Groq(api_key=key)
    completion = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[
            {"role": "system", "content": _REEL_PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt.strip()},
        ],
        temperature=0.25,
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content or ""
    parsed = _extract_json_object(raw)

    destination = str(parsed.get("destination") or "").strip()
    if not destination:
        raise RuntimeError("Groq did not return a destination.")

    origin = parsed.get("origin")
    if origin is not None:
        origin = str(origin).strip()
        if not origin:
            origin = None

    mode = str(parsed.get("mode") or "flight").strip().lower()
    if mode not in ("flight", "airport", "walking", "road", "train"):
        mode = "flight"

    refined_prompt = str(parsed.get("refined_prompt") or prompt).strip()

    image_keywords = str(parsed.get("image_keywords") or "").strip()
    video_keywords = str(parsed.get("video_keywords") or "").strip()

    if not image_keywords:
        image_keywords = "landmarks attractions city skyline scenic view"
    if not video_keywords:
        video_keywords = "cinematic travel motion vertical"

    return {
        "destination": destination,
        "origin": origin,
        "mode": mode,
        "refined_prompt": refined_prompt,
        "image_keywords": image_keywords,
        "video_keywords": video_keywords,
    }


_BLOG_MAX_IMAGES = 20
_BLOG_TITLE_MAX = 200
_BLOG_CAPTION_MAX = 800


_TRAVEL_BLOG_SYSTEM_PROMPT = """You are an elite travel copywriter who writes viral, shareable blog posts.

You will receive JSON with:
- title: the blog headline (use as the main theme)
- images: ordered list of absolute image URLs (https only)
- captions: parallel list of short notes per image (same length as images; may be empty strings)

TASK:
1) Read the title and every caption. Build one cohesive, emotional travel story.
2) Output ONLY valid JSON (no markdown fences) with exactly this shape:
   {"html": "<!DOCTYPE html>...full document..."}

RULES FOR THE HTML STRING:
- Must be a complete HTML5 document: <!DOCTYPE html>, <html lang="en">, <head>, <body>.
- <head> must include: <meta charset="utf-8">, <meta name="viewport" content="width=device-width, initial-scale=1">, <title>…</title> (use the user title),
  <meta name="description" content="…"> (one compelling SEO sentence, no quotes breaking the attribute),
  and a <style> block for a clean, modern, mobile-first article layout (max-width ~720–800px centered, readable font-size/line-height, generous spacing).
- Start <body> with <h1> using the user title (you may lightly polish for punch, do not change the destination/topic).
- Open with a short viral hook (1–2 paragraphs): immersive, fun, slightly dramatic, second-person or vivid narrator.
- For EACH image URL in order: insert <figure> with <img src="EXACT_URL_FROM_INPUT" alt="…" loading="lazy" /> and a <figcaption> if the matching caption is non-empty; weave story paragraphs before/after images naturally.
- Use ONLY the image URLs provided in the input JSON — copy them character-for-character into src attributes.
- Short paragraphs (2–4 sentences max each). Optional <h2> section breaks.
- Add a closing section with 2–4 suggested hashtags as plain text (e.g. line starting "Hashtags:") — no HTML script tags.
- Do NOT include <script> tags, iframes, or external stylesheets. Inline <style> only in <head>.
- Escape any < or & inside text content properly as HTML entities where needed.

TONE: viral travel blog — wonder, FOMO, sensory detail, light humor; avoid cliché lists; no fake facts about specific places beyond what captions imply.
"""


def generate_travel_blog_html(
    title: str,
    images: list[str],
    captions: list[str] | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Call Groq to produce a full HTML document (blog page) from a title, image URLs, and per-image captions.

    Returns ``{"html": str, "groq_model": str}``. Raises RuntimeError on missing key or invalid model output.
    """
    key = api_key or config.GROQ_API_KEY
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your environment or .env file."
        )

    t = (title or "").strip()
    if not t:
        raise RuntimeError("Blog title is required.")
    if len(t) > _BLOG_TITLE_MAX:
        t = t[:_BLOG_TITLE_MAX]

    imgs: list[str] = []
    for u in images or []:
        s = str(u).strip()
        if not s:
            continue
        if not (s.startswith("https://") or s.startswith("http://")):
            raise RuntimeError(f"Invalid image URL (must be http(s)): {s[:80]}")
        imgs.append(s)
    if not imgs:
        raise RuntimeError("At least one image URL is required.")
    if len(imgs) > _BLOG_MAX_IMAGES:
        imgs = imgs[:_BLOG_MAX_IMAGES]

    caps_in = list(captions) if captions else []
    caps: list[str] = []
    for i in range(len(imgs)):
        raw = caps_in[i] if i < len(caps_in) else ""
        c = str(raw or "").strip()
        if len(c) > _BLOG_CAPTION_MAX:
            c = c[:_BLOG_CAPTION_MAX]
        caps.append(c)

    payload = {"title": t, "images": imgs, "captions": caps}

    client = Groq(api_key=key)
    completion = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[
            {"role": "system", "content": _TRAVEL_BLOG_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        temperature=0.75,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content
    if not raw:
        raise RuntimeError("Groq returned an empty response.")

    try:
        parsed = _extract_json_object(raw)
    except json.JSONDecodeError as e:
        logger.error("Blog Groq JSON parse error: %s\nRaw: %s", e, raw[:800])
        raise RuntimeError("Groq response was not valid JSON.") from e

    html = parsed.get("html")
    if not isinstance(html, str) or not html.strip():
        raise RuntimeError('Groq JSON must include a non-empty string "html" field.')

    html_out = html.strip()
    if "<script" in html_out.lower():
        raise RuntimeError("Generated HTML contained disallowed script tags.")

    return {"html": html_out, "groq_model": config.GROQ_MODEL}
