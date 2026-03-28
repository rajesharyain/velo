# Travel Media API (`app/`)

Structured travel content from **Groq** (exactly 5 places, 4–6 Pexels-style queries each) plus **parallel Pexels** image/video search, deduped and capped per place.

## Run

**Standalone** (port 8010) from the repository root (`velo/`):

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

**Bundled in the main Velo UI** (port 8000): open `/ad-reels` and use **Fetch media**, or POST the same JSON to `http://127.0.0.1:8000/api/ad-reels/travel-media`.

Requires `.env` (or environment) with `GROQ_API_KEY` and `PEXELS_API_KEY` (same as `travel_instagram`).

## Example request

```bash
curl -s -X POST "http://127.0.0.1:8010/travel/media" ^
  -H "Content-Type: application/json" ^
  -d "{\"query\": \"Top places to visit in Portugal\", \"tags\": [\"sunset\"], \"download\": false}"
```

(PowerShell: use `curl.exe` or single-line JSON.)

## Sample response (shape)

```json
{
  "places": [
    {
      "name": "Lisbon",
      "type": "city",
      "highlights": ["Alfama", "Belém", "Miradouros"],
      "best_query": "Lisbon Alfama tram golden hour",
      "queries": ["Lisbon skyline Tagus sunset", "..."],
      "media": [
        {
          "type": "image",
          "url": "https://...",
          "photographer": "...",
          "width": 1920,
          "height": 1080,
          "tags": ["city", "sunset"],
          "local_path": null
        }
      ]
    }
  ],
  "groq_model": "llama-3.3-70b-versatile",
  "pexels_calls_used": 20,
  "cache_hits": 0
}
```

## Limits & env

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRAVEL_MEDIA_MAX_PEXELS_CALLS` | 20 | Max Pexels HTTP searches per request |
| `TRAVEL_MEDIA_PER_PLACE_MAX` | 10 | Max media items per place after dedupe |
| `TRAVEL_MEDIA_PEXELS_PER_PAGE` | 5 | `per_page` for Pexels |
| `TRAVEL_MEDIA_ORIENTATION` | landscape | Pexels `orientation` |
| `TRAVEL_MEDIA_CACHE_TTL` | 300 | In-memory cache TTL (seconds) |
| `TRAVEL_MEDIA_OUTPUT_DIR` | output/travel_media_downloads | When `download: true` |

OpenAPI: `http://127.0.0.1:8010/docs`
