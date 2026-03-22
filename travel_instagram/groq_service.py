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

SYSTEM_PROMPT = """You are a travel and tourism destination guide expert. You output ONLY valid JSON, no markdown.

Audience: Instagram travel guides — think vibes, scenery, and what a visitor actually sees.

Rules:
- 3 to 5 unique destinations (no duplicate names or near-duplicates).
- For EACH destination you MUST:
  - Classify scenery using scape_types: pick 2 to 5 tags from this vocabulary (use exact wording where possible):
    beach, coastline, ocean, tropical, island, mountains, alpine, volcano, forest, jungle, lake, river, waterfall, desert, canyon, countryside, vineyard, rice_terraces, savanna, glacier, city, skyline, historic_town, old_town, architecture, street_cafe, night_market, temple, castle, landmark, scenic_view, hiking_trail, road_trip, winter_snow, spring_bloom, sunset_view
  - vibe: one short phrase (max 18 words) describing mood and atmosphere (romantic, adventurous, laid-back, luxury, backpacker, family-friendly, etc.).
  - pexels_search_query: ONE line optimized for stock-photo search (English). Combine the place name PLUS concrete visual keywords from scape_types and vibe (e.g. "Santorini Greece white buildings blue dome sunset caldera", "Banff Canada turquoise lake mountain reflection"). No hashtags. 6 to 14 words ideal. This string is sent to Pexels before any carousel is built — make it specific to landscapes and scenery, not generic "travel".
  - caption: 20 to 40 words. Describe the destination for travelers: what it feels like, what you see and do, scenery and atmosphere. Informative and specific (not generic "amazing place"). No hashtags.
- hook: max 8 words, title for slide 1 (can nod to the theme’s scenery type).
- cta: max 10 words, final slide.
- hashtags: 8 to 12 tags WITHOUT the # symbol in JSON.
- Escape quotes in strings. ASCII-friendly punctuation."""

USER_PROMPT_TEMPLATE = """Theme / brief: {theme}

Return this JSON shape (all fields required on each destination object):
{{
  "hook": "string",
  "cta": "string",
  "hashtags": ["travel", "wanderlust"],
  "destinations": [
    {{
      "destination": "City or region, Country",
      "caption": "max twelve words",
      "scape_types": ["city", "coastline", "historic_town"],
      "vibe": "short mood and atmosphere phrase",
      "pexels_search_query": "place name plus scenery keywords for stock photos"
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


def _dedupe_destinations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in items:
        name = str(row.get("destination", "")).strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        cap = str(row.get("caption", "")).strip()
        words = cap.split()
        if len(words) > 48:
            cap = " ".join(words[:48])
        scape = _norm_scape_types(row.get("scape_types"))
        vibe = str(row.get("vibe", "")).strip()[:220]
        pq = str(row.get("pexels_search_query", "")).strip()[:300]
        if not pq:
            pq = _fallback_pexels_query(name, scape, vibe)
        out.append(
            {
                "destination": name,
                "caption": cap,
                "scape_types": scape,
                "vibe": vibe,
                "pexels_search_query": pq,
            }
        )
    return out


def _validate_and_trim(data: dict[str, Any], theme: str) -> dict[str, Any]:
    destinations = data.get("destinations") or []
    if not isinstance(destinations, list):
        destinations = []

    fixed = _dedupe_destinations([d for d in destinations if isinstance(d, dict)])

    if len(fixed) > config.DESTINATION_COUNT_MAX:
        fixed = fixed[: config.DESTINATION_COUNT_MAX]
    if len(fixed) < config.DESTINATION_COUNT_MIN:
        logger.warning(
            "Groq returned fewer than %s destinations; proceeding with %s",
            config.DESTINATION_COUNT_MIN,
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


def generate_travel_content(theme: str, api_key: str | None = None) -> dict[str, Any]:
    """
    Call Groq for tourism guide JSON: hook, CTA, hashtags, destinations with
    scape_types, vibe, and pexels_search_query for stock-image retrieval.
    """
    key = api_key or config.GROQ_API_KEY
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your environment or .env file."
        )

    client = Groq(api_key=key)
    user_msg = USER_PROMPT_TEMPLATE.format(theme=theme.strip())

    completion = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.85,
        max_tokens=2048,
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

    return _validate_and_trim(parsed, theme.strip())
