"""
Travel media API: Groq structured places + parallel Pexels (images + videos).

Run from repository root:
  uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from app.models.place import TravelMediaRequest, TravelMediaResponse
from app.services.aggregator import aggregate_travel_media
from app.utils.http_client import create_async_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = create_async_client(timeout=120.0)
    yield
    await app.state.http.aclose()


app = FastAPI(
    title="Travel Media",
    description="Structured travel places (Groq) + Pexels images/videos in parallel.",
    version="1.0.0",
    lifespan=lifespan,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_reels_ad_templates = Jinja2Templates(
    directory=str(_REPO_ROOT / "travel_instagram" / "templates")
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ad-reels", response_class=HTMLResponse)
async def ad_reels_page(request: Request) -> HTMLResponse:
    """Same UI as travel_instagram.web_app; use when this app is bound to port 8000."""
    return _reels_ad_templates.TemplateResponse(
        "reels_ad.html",
        {
            "request": request,
            "title": "Reels AD — Travel media",
            "nav_active": "ad_reels",
        },
    )


@app.post("/travel/media", response_model=TravelMediaResponse)
async def travel_media(body: TravelMediaRequest) -> TravelMediaResponse:
    client: httpx.AsyncClient = app.state.http
    try:
        return await aggregate_travel_media(
            body.query,
            client,
            extra_tags=body.tags,
            orientation=body.orientation or None,
            download=body.download,
        )
    except RuntimeError as e:
        logger.warning("Travel media pipeline failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except httpx.HTTPError as e:
        logger.exception("Upstream HTTP error")
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}") from e


@app.post("/api/ad-reels/travel-media", response_model=TravelMediaResponse)
async def ad_reels_travel_media(body: TravelMediaRequest) -> TravelMediaResponse:
    """POST target for the Reels AD HTML page at ``/ad-reels``."""
    return await travel_media(body)
