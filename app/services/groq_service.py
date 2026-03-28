"""
Groq: structured travel places JSON (strict schema for Pexels queries).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app import config

logger = logging.getLogger(__name__)

PLACES_SYSTEM_PROMPT = """You are a travel research assistant. Output ONLY valid JSON (no markdown, no commentary).

The user describes where they want to go or what list they want (e.g. top places in a country or city).

You MUST return this exact shape:
{
  "places": [
    {
      "name": "string",
      "type": "city|region|island|area|landmark_area",
      "highlights": ["short highlight 1", "short highlight 2", "short highlight 3"],
      "best_query": "single best English line for stock photo search",
      "caption_text": "Instagram Reels on-video blurb under the place name: MAX 12-15 words. START with a strong hook (curiosity, emotion, or punch). Simple, emotional, scroll-stopping — NOT a long description. No hashtags, no search keywords.",
      "queries": ["query1", "query2", "query3", "query4"]
    }
  ]
}

Hard rules:
- Always return EXACTLY 5 objects in "places".
- "caption_text": required on every place. Hard cap 12-15 words. Open with a hook that makes viewers stop scrolling; keep language simple and emotional. Never write a travel-guide paragraph. Do not repeat the place name only; do not paste search keywords. This appears on-video under the location title.
- Each place must have 4 to 6 strings in "queries" (inclusive). "best_query" is separate and MUST NOT be duplicated inside "queries".
- Every query string must: include the place or area name (or unambiguous local name), mention a landmark OR vibe from highlights OR a visual scene, and include at least one visual keyword when natural (e.g. sunset, aerial, drone, waterfront, night, historic, coastline, architecture).
- "best_query" should be the single strongest 4–10 word English stock-search line for that place (location + iconic visual).
- "highlights": 3 to 6 short phrases per place.
- Use ASCII-friendly punctuation in JSON strings; escape double quotes inside strings.
- Types: prefer "city" for cities, "region" for areas like Algarve, "island" for islands, "landmark_area" for compact historic quarters.
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


async def generate_places(user_input: str, *, client: httpx.AsyncClient) -> dict[str, Any]:
    """
    Call Groq chat completions (OpenAI-compatible) and return parsed JSON dict
    with key "places" (list of place objects).
    """
    key = config.GROQ_API_KEY
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set.")

    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": PLACES_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"User request:\n{(user_input or '').strip()}",
            },
        ],
        "temperature": 0.35,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    r = await client.post(
        config.GROQ_CHAT_COMPLETIONS_URL,
        headers=headers,
        json=payload,
    )
    r.raise_for_status()
    data = r.json()
    raw = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    if not raw:
        raise RuntimeError("Groq returned empty content.")

    try:
        parsed = _extract_json_object(raw)
    except json.JSONDecodeError as e:
        logger.error("Groq JSON parse error: %s | snippet: %s", e, raw[:400])
        raise RuntimeError("Groq response was not valid JSON.") from e

    return parsed
