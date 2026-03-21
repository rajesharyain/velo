"""
Groq LLM integration: travel themes to structured JSON (destinations, hook, hashtags, CTA).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from groq import Groq

from travel_instagram import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a travel social media copywriter. You output ONLY valid JSON, no markdown.
Rules:
- 3 to 5 unique destinations (no duplicate names or near-duplicates).
- Each caption: maximum 12 words, punchy, no hashtags in caption text.
- hook is one short title for the first carousel slide (max 8 words).
- cta is a closing line for the final slide (max 10 words).
- hashtags: 8 to 12 relevant trending-style hashtags WITHOUT the hash symbol in JSON strings.
- Escape quotes inside strings properly. Use ASCII-friendly punctuation."""

USER_PROMPT_TEMPLATE = """Theme: {theme}

Return this exact JSON shape:
{{
  "hook": "string",
  "cta": "string",
  "hashtags": ["travel", "wanderlust"],
  "destinations": [
    {{"destination": "City, Country", "caption": "short engaging line under twelve words"}}
  ]
}}"""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


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
        if len(words) > 12:
            cap = " ".join(words[:12])
        out.append({"destination": name, "caption": cap})
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
    Call Groq to produce hook, CTA, hashtags, and 3 to 5 destinations with captions.
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
        max_tokens=1024,
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
