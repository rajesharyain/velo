from __future__ import annotations

import httpx


def create_async_client(*, timeout: float = 90.0) -> httpx.AsyncClient:
    """Shared async HTTP client (timeouts tuned for Groq + Pexels)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
    )
