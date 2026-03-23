"""
Small web UI for the travel Instagram generator.

Run from project root:
  uvicorn travel_instagram.web_app:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from pydantic import BaseModel, Field
from starlette.templating import Jinja2Templates

from travel_instagram import config
from travel_instagram import pipeline
from travel_instagram import reels_catalog
from travel_instagram.instagram_post_export import safe_carousel_run_dir
from travel_instagram.instapost.router import router as instapost_router

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(
    title="Travel Instagram Generator",
    description="Generate carousels and reels from a theme (Groq + Pexels + FFmpeg).",
    version="1.0.0",
)

config.ensure_output_dirs()
app.mount(
    "/media",
    StaticFiles(directory=str(config.OUTPUT_DIR)),
    name="media",
)

app.include_router(instapost_router)


def _abs_path_under_output(p: str | Path) -> Path | None:
    try:
        resolved = Path(p).resolve()
        out = config.OUTPUT_DIR.resolve()
        resolved.relative_to(out)
        return resolved
    except (ValueError, OSError):
        return None


def _to_media_url(abs_path: str | Path) -> str | None:
    p = _abs_path_under_output(abs_path)
    if p is None:
        return None
    rel = p.relative_to(config.OUTPUT_DIR.resolve())
    return "/media/" + rel.as_posix()


def _enrich_summary_for_web(summary: dict[str, Any]) -> dict[str, Any]:
    out = dict(summary)
    outputs = dict(summary.get("outputs") or {})

    slides = outputs.get("carousel_slides") or []
    outputs["carousel_slides_urls"] = [_to_media_url(s) for s in slides]

    reel = outputs.get("reel_video")
    outputs["reel_video_url"] = _to_media_url(reel) if reel else None

    sj = outputs.get("summary_json")
    outputs["summary_json_url"] = _to_media_url(sj) if sj else None

    out["outputs"] = outputs
    return out


class GenerateBody(BaseModel):
    theme: str = Field(..., min_length=1, max_length=500)
    music_track_id: str | None = Field(
        default=None,
        max_length=512,
        description="Relative path under music/, __none__ for silence, null/__auto__ for .env/first file.",
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "title": "Travel Instagram Generator",
            "nav_active": "create",
        },
    )


@app.get("/reels", response_class=HTMLResponse)
async def reels_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "reels.html",
        {
            "request": request,
            "title": "Reel library",
            "nav_active": "reels",
        },
    )


def _carousel_slides_media_urls(run_dir: Path) -> list[str]:
    """Web URLs for existing carousel slide JPEGs under this run (original 9:16 assets)."""
    sj = run_dir / "summary.json"
    if not sj.is_file():
        return []
    try:
        data = json.loads(sj.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    outputs = data.get("outputs") or {}
    slides = outputs.get("carousel_slides") or []
    if not isinstance(slides, list):
        return []
    urls: list[str] = []
    for s in slides:
        p = Path(str(s))
        if not p.is_file():
            continue
        u = _to_media_url(p)
        if u:
            urls.append(u)
    return urls


@app.get("/carousel/{run_id}", response_class=HTMLResponse)
async def carousel_run_page(request: Request, run_id: str) -> HTMLResponse:
    """All original carousel slide images for one pipeline run."""
    config.ensure_output_dirs()
    try:
        run_dir = safe_carousel_run_dir(run_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run not found.")
    sj = run_dir / "summary.json"
    if not sj.is_file():
        raise HTTPException(status_code=404, detail="No summary.json for this run.")
    try:
        data = json.loads(sj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=404, detail="Invalid summary.json.") from e
    content = data.get("content") or {}
    theme = (data.get("theme") or "").strip()
    hook = (content.get("hook") or "").strip()
    slides_urls = _carousel_slides_media_urls(run_dir)
    page_title = hook or theme or "Carousel slides"
    return templates.TemplateResponse(
        "carousel_run.html",
        {
            "request": request,
            "title": page_title,
            "nav_active": "reels",
            "run_id": run_id,
            "theme": theme,
            "carousel_slides_urls": slides_urls,
            "slide_count": len(slides_urls),
        },
    )


@app.get("/api/reels")
async def api_reels() -> JSONResponse:
    """All reels from ``carousel/*/summary.json`` (newest first)."""
    config.ensure_output_dirs()
    return JSONResponse(content={"reels": reels_catalog.list_reels()})


@app.get("/api/reels/export.xlsx")
async def api_reels_export_xlsx() -> StreamingResponse:
    """Excel workbook of the reel library."""
    config.ensure_output_dirs()
    rows = reels_catalog.list_reels()
    wb = Workbook()
    ws = wb.active
    ws.title = "Reels"
    headers = [
        "Run ID",
        "Theme",
        "Title (hook)",
        "Hashtags",
        "Generated (UTC)",
        "Reel filename",
        "Reel file present",
        "Reel path (web)",
    ]
    for col, h in enumerate(headers, start=1):
        ws.cell(row=1, column=col, value=h)
    for i, r in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=r.get("run_id") or "")
        ws.cell(row=i, column=2, value=r.get("theme") or "")
        ws.cell(row=i, column=3, value=r.get("title") or "")
        ws.cell(row=i, column=4, value=r.get("hashtags") or "")
        ws.cell(row=i, column=5, value=r.get("generated_at") or "")
        ws.cell(row=i, column=6, value=r.get("reel_filename") or "")
        ws.cell(row=i, column=7, value="yes" if r.get("reel_exists") else "no")
        ws.cell(row=i, column=8, value=r.get("reel_video_url") or "")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"velo_reels_{stamp}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/generate")
async def api_generate(body: GenerateBody) -> JSONResponse:
    theme = body.theme.strip()
    if not theme:
        raise HTTPException(status_code=400, detail="Theme is required.")

    try:
        summary = await asyncio.to_thread(
            pipeline.run_pipeline,
            theme,
            body.music_track_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("Pipeline failed")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected pipeline error")
        raise HTTPException(status_code=500, detail=str(e)) from e

    enriched = _enrich_summary_for_web(summary)
    return JSONResponse(content=enriched)


@app.get("/api/music-tracks")
async def api_music_tracks() -> JSONResponse:
    """Filenames under ``music/`` for the background-music dropdown."""
    config.ensure_output_dirs()
    return JSONResponse(content={"tracks": config.list_music_tracks()})


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "travel_instagram.web_app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
