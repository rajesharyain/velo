"""
Instagram Graph API: publish Reels (server-side; tokens stay in .env).
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from travel_instagram import config

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v21.0"


def instagram_credentials_configured() -> bool:
    uid = (config.IG_USER_ID or "").strip()
    tok = (config.IG_ACCESS_TOKEN or "").strip()
    return bool(uid and tok)


def _graph_error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error") or {}
        return str(err.get("message") or data)
    except Exception:
        return resp.text or f"HTTP {resp.status_code}"


def publish_reel(
    *,
    video_url: str,
    caption: str,
    wait_after_create_sec: float = 22.0,
) -> dict[str, Any]:
    """
    Create a Reels media container, wait for processing, then ``media_publish``.

    ``video_url`` must be **https** and publicly reachable by Meta's servers.
    """
    if not instagram_credentials_configured():
        raise RuntimeError(
            "Instagram is not configured. Set IG_USER_ID and IG_ACCESS_TOKEN in .env.",
        )
    vu = video_url.strip()
    if not vu.lower().startswith("https://"):
        raise ValueError(
            "Instagram requires an HTTPS video URL that Meta can fetch "
            "(use ngrok / a tunnel and PUBLIC_APP_BASE_URL).",
        )

    ig_id = (config.IG_USER_ID or "").strip()
    token = (config.IG_ACCESS_TOKEN or "").strip()
    cap = (caption or "").strip()
    if not cap:
        cap = "."

    base = f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_id}"
    params_create = {
        "media_type": "REELS",
        "video_url": vu,
        "caption": cap,
        "access_token": token,
    }
    url_create = f"{base}/media?{urlencode(params_create)}"

    with httpx.Client(timeout=120.0) as client:
        r1 = client.post(url_create)
        if r1.status_code >= 400:
            msg = _graph_error_message(r1)
            logger.error("Instagram media create failed: %s", msg)
            raise RuntimeError(f"Instagram media create failed: {msg}")

        j1 = r1.json()
        creation_id = j1.get("id")
        if not creation_id:
            raise RuntimeError(f"Instagram: unexpected response (no id): {j1}")

        # Poll until Instagram finishes processing the container (status = FINISHED)
        url_status = f"https://graph.facebook.com/{GRAPH_VERSION}/{creation_id}?fields=status_code&access_token={token}"
        max_wait = 180
        poll_interval = 8
        elapsed = 0
        status_code = "IN_PROGRESS"
        logger.info("Instagram: polling container status (max %ds)…", max_wait)
        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval
            rs = client.get(url_status)
            if rs.status_code < 400:
                status_code = rs.json().get("status_code", "UNKNOWN")
                logger.info("Instagram container status: %s (%ds elapsed)", status_code, elapsed)
                if status_code == "FINISHED":
                    break
                if status_code in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"Instagram container processing failed: {status_code}")

        if status_code != "FINISHED":
            raise RuntimeError(
                f"Instagram container not ready after {max_wait}s (status={status_code}). "
                "The video may be too large or the URL unreachable by Meta.",
            )

        params_pub = {"creation_id": creation_id, "access_token": token}
        url_pub = f"{base}/media_publish?{urlencode(params_pub)}"
        r2 = client.post(url_pub)
        if r2.status_code >= 400:
            msg = _graph_error_message(r2)
            logger.error("Instagram publish failed: %s", msg)
            raise RuntimeError(f"Instagram publish failed: {msg}")
        return r2.json()
