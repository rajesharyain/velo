# Travel Instagram Generator

Automated pipeline that uses **Groq** (LLM) for travel copy and **Pexels** for media, then builds:

- **Carousel**: 5–10 JPEG slides at **1080×1350** with title, destination captions, hashtags slide, and CTA  
- **Reel**: **1080×1920** MP4 built from the **first five carousel slides** (same JPEGs as the feed, with hook/destination/hashtag/CTA text). PIL decodes each slide, streams **raw RGB24** into FFmpeg, then libx264. Optional background music  

## Requirements

- **Python 3.10+**
- **FFmpeg** — either on your `PATH`, or set **`FFMPEG_PATH`** in `.env` to the install folder (e.g. `R:\projects\ffmpeg-8.1`, which contains `bin\ffmpeg.exe`) or to the full path of `ffmpeg.exe` ([download](https://ffmpeg.org/download.html))  
- **Groq API key** ([console](https://console.groq.com/))  
- **Pexels API key** ([Pexels API](https://www.pexels.com/api/))  

## Setup

```powershell
cd r:\projects\velo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env: set GROQ_API_KEY and PEXELS_API_KEY
```

Optional in `.env`:

- `GROQ_MODEL` — default `llama-3.3-70b-versatile`  
- `REEL_MUSIC_PATH` — path to MP3/WAV for reel audio (otherwise video has no audio)  
- `REEL_FRAME_COUNT` — number of stills stitched into the reel (default **5**)  
- `REEL_TOTAL_SECONDS` — total reel length in seconds (default **15**, clamped 5–30)  
- `OUTPUT_DIR` — override output root (default `./output`)  

## Web UI

Install dependencies (includes FastAPI and Uvicorn), then from the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn travel_instagram.web_app:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), enter a theme, and submit. Generated images and video are served under `/media/...` for preview and download. The API endpoint `POST /api/generate` accepts JSON `{"theme": "your theme"}` and returns the same summary object as the CLI (plus `*_url` fields for the browser).

## Run (CLI)

Single theme:

```powershell
python -m travel_instagram.cli --theme "hidden beaches in Europe"
```

Batch (one theme per line; `#` starts a comment):

```powershell
python -m travel_instagram.cli --batch themes.txt
```

Verbose FFmpeg logging:

```powershell
python -m travel_instagram.cli -t "budget travel Asia" -v
```

## Output layout

- `output/carousel/<run_id>/carousel/slide_01.jpg` …  
- `output/carousel/<run_id>/downloads/` — cached Pexels files for that run  
- `output/carousel/<run_id>/summary.json` — full JSON summary  
- `output/reels/reel_<slug>.mp4`  

The CLI prints the same summary as JSON on stdout.

## Module layout

| Module | Role |
|--------|------|
| `travel_instagram/groq_service.py` | Theme → JSON: destinations with `scape_types`, `vibe`, `pexels_search_query` + hook/CTA/hashtags |
| `travel_instagram/pexels_service.py` | Images from Groq-tuned query (fallback to place name if empty results) |
| `travel_instagram/media_processor.py` | PIL carousel; reel = raw RGB → FFmpeg libx264 + optional audio |
| `travel_instagram/pipeline.py` | Downloads, orchestration, `summary.json` |
| `travel_instagram/cli.py` | argparse CLI |

## Behavior notes

- **Reel** stitches **carousel slide JPEGs** (not raw downloads), so on-screen text matches the carousel.  
- **Groq** labels each stop with **scape_types** (beach, mountains, city, …), a **vibe** line, and a **pexels_search_query**; **Pexels** runs on that query first (then falls back to the place name if needed).  
- **Duplicates**: destinations deduped by name (case-insensitive).  
- **Captions**: trimmed to 12 words in validation.  
- **Media**: Pexels portrait search first; image search falls back to any orientation if empty.  

## License

Use complies with [Pexels license](https://www.pexels.com/license/) and your Groq/API terms. Generated assets are for your own publishing workflow.
# velo
