"""
Facebook Graph API: publish a video to a Page from a public HTTPS URL (file_url).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from travel_instagram import config

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v21.0"


def facebook_page_credentials_configured() -> bool:
    pid = (config.FB_PAGE_ID or "").strip()
    tok = (config.FB_PAGE_ACCESS_TOKEN or "").strip()
    return bool(pid and tok)


def _graph_error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error") or {}
        return str(err.get("message") or data)
    except Exception:
        return resp.text or f"HTTP {resp.status_code}"


def publish_page_video(
    *,
    video_url: str,
    title: str | None,
    description: str | None,
) -> dict[str, Any]:
    """
    POST /{page-id}/videos with ``file_url`` (HTTPS, publicly reachable by Meta).

    Requires a **Page** access token with ``pages_manage_posts``,
    ``pages_read_engagement``, and ``pages_show_list`` (and the user must have
    CREATE_CONTENT on the Page).
    """
    if not facebook_page_credentials_configured():
        raise RuntimeError(
            "Facebook Page is not configured. Set FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN in .env.",
        )
    vu = video_url.strip()
    if not vu.lower().startswith("https://"):
        raise ValueError(
            "Facebook requires an HTTPS video URL that Meta can fetch "
            "(use ngrok / a tunnel and PUBLIC_APP_BASE_URL).",
        )

    page_id = (config.FB_PAGE_ID or "").strip()
    token = (config.FB_PAGE_ACCESS_TOKEN or "").strip()
    desc = (description or "").strip() or "."
    tit = (title or "").strip()

    params: dict[str, str] = {
        "file_url": vu,
        "description": desc[:5000],
        "access_token": token,
        "published": "true",
    }
    if tit:
        params["title"] = tit[:255]

    base = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/videos"
    url = f"{base}?{urlencode(params)}"

    with httpx.Client(timeout=180.0) as client:
        r = client.post(url)
        if r.status_code >= 400:
            msg = _graph_error_message(r)
            logger.error("Facebook Page video create failed: %s", msg)
            raise RuntimeError(f"Facebook Page video failed: {msg}")
        return r.json()
