from __future__ import annotations

import json
import logging
import re
from typing import Any

from groq import Groq

from travel_instagram import config

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a viral travel reel script writer.

You output ONLY valid JSON (no markdown, no code fences).

Constraints:
- Short sentences. Strong curiosity and emotional tone.
- Hook must be max 10 words.
- hashtags must be 8–12 tags, WITHOUT the '#' symbol.
- visual must describe what the viewer sees (clip ideas), optimized for stock footage search.
- value must include a deal-like value (price range or "from $" style) plus why visit.
- cta must drive engagement (save/share/comment) within max ~10 words.
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\\s*```$", "", cleaned)
    return json.loads(cleaned)


def _trim_to_max_words(s: str, max_words: int) -> str:
    words = str(s or "").strip().split()
    if len(words) <= max_words:
        return str(s).strip()
    return " ".join(words[:max_words]).strip()


def _trim_hashtags(tags: list[Any]) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        st = str(t).strip().lstrip("#")
        if not st:
            continue
        if st not in out:
            out.append(st)
    return out[:12]


def generate_scripts(destination_query: str, variations: int = 1, api_key: str | None = None) -> list[dict[str, Any]]:
    """
    Generate InstaPost reel scripts.

    Returns a list of scripts with keys:
    hook, visual, value, cta, hashtags.
    """
    key = api_key or config.GROQ_API_KEY
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your environment or .env file.")
    dest = destination_query.strip()
    if not dest:
        raise ValueError("destination_query must be non-empty.")

    client = Groq(api_key=key)

    if variations <= 1:
        user_msg = f"""Create a viral Instagram travel reel script.

Follow this structure:
1. Hook (max 10 words, highly attention grabbing)
2. Visual (what viewer sees)
3. Value (price, why visit, or tip)
4. CTA (engagement driven)

Tone:
- वायरल
- emotional
- curiosity-driven
- short sentences

Topic: {dest}

Return JSON only with EXACT shape:
{{
  "hook": "string",
  "visual": "string",
  "value": "string",
  "cta": "string",
  "hashtags": ["travel", "wanderlust"]
}}
"""
    else:
        user_msg = f"""Create {variations} distinct viral Instagram travel reel scripts.

Follow this structure for EACH script:
1. Hook (max 10 words, highly attention grabbing)
2. Visual (what viewer sees)
3. Value (price, why visit, or tip)
4. CTA (engagement driven)

Tone:
- वायरल
- emotional
- curiosity-driven
- short sentences

Topic: {dest}

Return JSON only with this EXACT shape:
{{
  "scripts": [
    {{
      "hook": "string",
      "visual": "string",
      "value": "string",
      "cta": "string",
      "hashtags": ["travel", "wanderlust"]
    }}
  ]
}}
"""

    completion = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.9,
        max_tokens=1100,
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content
    if not raw:
        raise RuntimeError("Groq returned an empty response.")

    parsed = _extract_json_object(raw)

    if variations <= 1:
        scripts = [parsed]
    else:
        scripts = parsed.get("scripts") or []

    out: list[dict[str, Any]] = []
    for sc in scripts:
        if not isinstance(sc, dict):
            continue
        hook = _trim_to_max_words(sc.get("hook") or "", 10)
        value = str(sc.get("value") or "").strip()
        visual = str(sc.get("visual") or "").strip()
        cta = _trim_to_max_words(sc.get("cta") or "", 10)
        hashtags = _trim_hashtags(sc.get("hashtags") or [])
        if not hook or not value or not visual or not cta or not hashtags:
            continue
        out.append(
            {
                "hook": hook,
                "visual": visual,
                "value": value,
                "cta": cta,
                "hashtags": hashtags,
            }
        )

    if not out:
        raise RuntimeError("Groq returned no usable scripts.")
    return out

