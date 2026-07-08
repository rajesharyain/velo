"""
YouTube Shorts publisher using the YouTube Data API v3.

Credentials required in .env:
  YOUTUBE_CLIENT_ID      — OAuth 2.0 client ID (Desktop app)
  YOUTUBE_CLIENT_SECRET  — OAuth 2.0 client secret
  YOUTUBE_REFRESH_TOKEN  — long-lived refresh token

Run scripts/youtube_auth.py once to obtain the refresh token.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from travel_instagram import config

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_YOUTUBE_VIDEO_BASE = "https://www.youtube.com/shorts/"


def youtube_credentials_configured() -> bool:
    return bool(config.YOUTUBE_CLIENT_ID and config.YOUTUBE_CLIENT_SECRET and config.YOUTUBE_REFRESH_TOKEN)


def _get_access_token() -> str:
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "client_id": config.YOUTUBE_CLIENT_ID,
            "client_secret": config.YOUTUBE_CLIENT_SECRET,
            "refresh_token": config.YOUTUBE_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if not resp.is_success:
        raise RuntimeError(f"YouTube token refresh failed: {resp.status_code} {resp.text[:300]}")
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {resp.text[:300]}")
    return token


def get_channel_info() -> dict:
    """Return the YouTube channel name, ID and URL for the configured account."""
    access_token = _get_access_token()
    resp = httpx.get(
        _CHANNELS_URL,
        params={"part": "snippet,statistics", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if not resp.is_success:
        raise RuntimeError(f"YouTube channels API failed: {resp.status_code} {resp.text[:300]}")
    items = resp.json().get("items", [])
    if not items:
        return {"error": "No YouTube channel found for this account."}
    ch = items[0]
    snippet = ch.get("snippet", {})
    stats = ch.get("statistics", {})
    custom = snippet.get("customUrl", "")
    channel_id = ch.get("id", "")
    return {
        "channel_name": snippet.get("title", ""),
        "channel_id": channel_id,
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "custom_url": f"https://www.youtube.com/{custom}" if custom else "",
        "subscribers": stats.get("subscriberCount", "—"),
        "video_count": stats.get("videoCount", "—"),
    }


def publish_short(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy: str = "public",
) -> dict:
    """
    Upload a 9:16 video as a YouTube Short.

    Returns dict with ``youtube_video_id`` and ``youtube_url``.
    """
    if not youtube_credentials_configured():
        raise RuntimeError(
            "YouTube credentials not configured. "
            "Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN in .env."
        )

    if not video_path.is_file():
        raise RuntimeError(f"Video file not found: {video_path}")

    access_token = _get_access_token()

    # #Shorts in title helps YouTube classify it as a Short
    short_title = title.strip()
    if "#Shorts" not in short_title and "#shorts" not in short_title.lower():
        short_title = short_title[:94] + " #Shorts"
    short_title = short_title[:100]

    metadata = {
        "snippet": {
            "title": short_title,
            "description": description,
            "tags": (tags or []) + ["Shorts", "travel", "reels"],
            "categoryId": "19",  # Travel & Events
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    file_size = video_path.stat().st_size

    # Step 1 — initiate resumable upload session
    init_resp = httpx.post(
        _UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_size),
        },
        content=json.dumps(metadata).encode(),
        timeout=30,
    )
    if not init_resp.is_success:
        raise RuntimeError(
            f"YouTube upload init failed: {init_resp.status_code} {init_resp.text[:400]}"
        )

    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube did not return an upload URL.")

    # Step 2 — stream the video
    logger.info("Uploading %s (%d MB) to YouTube…", video_path.name, file_size // 1_000_000)
    with open(video_path, "rb") as fh:
        upload_resp = httpx.put(
            upload_url,
            content=fh.read(),
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
            },
            timeout=600,
        )

    if not upload_resp.is_success:
        raise RuntimeError(
            f"YouTube upload failed: {upload_resp.status_code} {upload_resp.text[:400]}"
        )

    data = upload_resp.json()
    video_id = data.get("id", "")
    return {
        "youtube_video_id": video_id,
        "youtube_url": _YOUTUBE_VIDEO_BASE + video_id if video_id else "",
        "title": short_title,
        "status": data.get("status", {}).get("uploadStatus", ""),
    }
