from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.templating import Jinja2Templates

from travel_instagram import config
from travel_instagram.instapost import pipeline

logger = logging.getLogger(__name__)

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class InstaPostGenerateBody(BaseModel):
    destination_query: str = Field(..., min_length=1, max_length=200)
    variations: int = Field(default=1, ge=1, le=5)
    music_track_id: str | None = Field(
        default=None,
        max_length=512,
        description="Relative path under music/, __none__ for silence, null/__auto__ for .env/first file.",
    )


@router.get("/instapost", response_class=HTMLResponse)
async def instapost_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "instapost.html",
        {
            "request": request,
            "title": "InstaPost",
            "nav_active": "instapost",
        },
    )


@router.post("/instapost/api/generate")
async def instapost_generate(body: InstaPostGenerateBody) -> JSONResponse:
    try:
        summary = await pipeline.generate_instapost(
            destination_query=body.destination_query,
            variations=body.variations,
            music_track_id=body.music_track_id,
        )
        return JSONResponse(content=summary)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("InstaPost failed")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("InstaPost unexpected error")
        raise HTTPException(status_code=500, detail=str(e)) from e

