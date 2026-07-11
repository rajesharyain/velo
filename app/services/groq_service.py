"""
Groq: structured travel places JSON (strict schema for Pexels queries).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx

from app import config

logger = logging.getLogger(__name__)

_PLACES_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PLACES_CACHE_TTL: float = 6 * 3600  # 6 hours


def _places_cache_key(user_input: str) -> str:
    return hashlib.md5(user_input.strip().lower().encode()).hexdigest()

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
      "caption_text": "10-12 words MAX. Raw emotion or sensory detail — NO place/country name, NO 'discover/uncover/explore/experience'. Feel, don't describe. E.g.: 'Nothing prepares you for your first sunset here.'",
      "queries": ["query1", "query2", "query3", "query4"]
    }
  ]
}

Hard rules:
- Always return EXACTLY 5 objects in "places".
- "caption_text": STRICT RULES — this appears on-video under the location title. Hard cap 10-12 words. FORBIDDEN: place name, country name, region name, any search-style phrasing, "discover", "uncover", "explore", "experience", "hidden gems of X". REQUIRED: start with raw emotion, curiosity, or sensory detail — make the viewer FEEL something before they think. Write like a friend whispering a secret, not a tour guide. Examples of GOOD caption_text: "Nothing prepares you for your first sunset here.", "Locals keep this one off every map.", "You'll want to move here after one afternoon.", "This place broke my idea of what beauty means.", "Once you see it, nowhere else feels quite right."
- Each place must have 4 to 6 strings in "queries" (inclusive). "best_query" is separate and MUST NOT be duplicated inside "queries".
- Every query string must: include the place or area name (or unambiguous local name), mention a landmark OR vibe from highlights OR a visual scene, and include at least one visual keyword when natural (e.g. sunset, aerial, drone, waterfront, night, historic, coastline, architecture).
- "best_query" CRITICAL RULE: MUST contain the exact place name or landmark name. It is the PRIMARY Pexels search — the reel title shown to viewers comes from this place's name, so the media returned MUST visually match. Forbidden: generic country/region terms alone (e.g. "Ireland coastal landscape" for Cliffs of Moher). Required: the specific place name + one strong visual scene (e.g. "Cliffs of Moher aerial view Ireland", "Alhambra palace Granada Spain", "Ha Long Bay limestone karsts Vietnam"). If the place IS a city, include its most iconic landmark or district (e.g. "Dublin city Temple Bar night" not just "Dublin Ireland").
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

    ck = _places_cache_key(user_input)
    hit = _PLACES_CACHE.get(ck)
    if hit is not None:
        ts, cached = hit
        if time.monotonic() - ts < _PLACES_CACHE_TTL:
            logger.info("Groq places cache hit for %r — skipping API call", user_input[:60])
            return cached

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

    _PLACES_CACHE[ck] = (time.monotonic(), parsed)
    return parsed
