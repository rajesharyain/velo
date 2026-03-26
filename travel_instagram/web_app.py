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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from pydantic import BaseModel, Field
from starlette.templating import Jinja2Templates

from travel_instagram import config
from travel_instagram import pipeline
from travel_instagram import groq_service
from travel_instagram import manual_reel_builder
from travel_instagram import mcp_reel_tool
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

app.mount(
    "/music",
    StaticFiles(directory=str(config.MUSIC_LIBRARY_DIR)),
    name="music",
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


class McpReelBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=800)
    music_track_id: str | None = Field(
        default=None,
        max_length=512,
        description="Relative path under music/ for audio, __none__ for silence, or null/__auto__ for automatic.",
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


@app.get("/mcp-reels", response_class=HTMLResponse)
async def mcp_reels_page(request: Request) -> HTMLResponse:
    """Isolated UI for price reels via MCP tool."""
    return templates.TemplateResponse(
        "mcp_reels.html",
        {
            "request": request,
            "title": "Price Reels",
            "nav_active": "mcp_reels",
        },
    )


@app.get("/upload-reel", response_class=HTMLResponse)
async def upload_reel_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "upload_reel.html",
        {
            "request": request,
            "title": "Generate Reel from Media",
            "nav_active": "upload_reel",
        },
    )


@app.post("/api/mcp-reels/generate")
async def api_mcp_reels_generate(body: McpReelBody) -> JSONResponse:
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required.")

    music_track_id = body.music_track_id
    if music_track_id == "__auto__":
        music_track_id = None
    if isinstance(music_track_id, str) and music_track_id.strip() == "":
        music_track_id = None

    try:
        refined: dict[str, Any] | None = None
        try:
            refined = await asyncio.to_thread(groq_service.parse_reel_prompt, prompt)
        except Exception:
            refined = None
        refined_prompt = ((refined or {}).get("refined_prompt") or prompt).strip()
        search_destination = (refined or {}).get("destination")
        search_origin = (refined or {}).get("origin")
        search_mode = (refined or {}).get("mode")
        image_keywords = (refined or {}).get("image_keywords")
        video_keywords = (refined or {}).get("video_keywords")

        res = await asyncio.to_thread(
            mcp_reel_tool.generate_travel_reel_from_prompt,
            refined_prompt,
            music_path=music_track_id,
            search_destination=search_destination,
            search_origin=search_origin,
            search_mode=search_mode,
            image_keywords=image_keywords,
            video_keywords=video_keywords,
        )

        out = dict(res)
        out["video_url"] = _to_media_url(out.get("output_path") or "") or None
        out["refined"] = refined
        return JSONResponse(content=out)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("MCP reel generation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/upload-reel/generate")
async def api_upload_reel_generate(
    files: list[UploadFile] = File(...),
    captions_json: str = Form(default="[]"),
    music_track_id: str | None = Form(default=None),
    transition_type: str = Form(default="auto"),
    transition_speed: str = Form(default="auto"),
) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one file.")

    if music_track_id == "__auto__":
        music_track_id = None
    if isinstance(music_track_id, str) and music_track_id.strip() == "":
        music_track_id = None

    try:
        parsed = json.loads(captions_json or "[]")
        captions = [str(x or "") for x in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="Invalid captions_json payload.") from e

    allowed = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v", ".webm"}
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    temp_dir = config.OUTPUT_DIR / "manual_reels" / f"tmp_{ts}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    media_paths: list[Path] = []
    try:
        for i, up in enumerate(files):
            suffix = Path(up.filename or "").suffix.lower()
            if suffix not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type '{suffix}' for '{up.filename}'.",
                )
            name = f"{i:02d}_{Path(up.filename or 'asset').stem}{suffix}"
            out = temp_dir / name
            data = await up.read()
            out.write_bytes(data)
            media_paths.append(out)

        res = await asyncio.to_thread(
            manual_reel_builder.build_manual_reel,
            uploads_dir=temp_dir,
            media_paths=media_paths,
            captions=captions,
            music_track_id=music_track_id,
            transition_type=transition_type,
            transition_speed=transition_speed,
        )
        out = dict(res)
        out["video_url"] = _to_media_url(out.get("output_path") or "") or None
        return JSONResponse(content=out)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("Upload reel generation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


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
