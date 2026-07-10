"""
Orchestrate Groq → normalized places → parallel Pexels → deduped per-place media.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Literal

import httpx

from app import config
from app.models.media import MediaItem, MediaRecord
from app.models.place import (
    GroqPlaceStructured,
    GroqPlacesResponse,
    PexelsSearchPlanItem,
    PlaceInput,
    PlaceWithMedia,
    SelectedClip,
    TravelMediaResponse,
)
from app.services.groq_service import generate_places
from app.services.pexels_service import search_media

logger = logging.getLogger(__name__)

MediaKind = Literal["image", "video"]


def _augment_query(query: str, tags: list[str]) -> str:
    q = " ".join((query or "").split()).strip()
    extra = " ".join(t.strip() for t in tags if t and str(t).strip())
    if not extra:
        return q
    return f"{q} {extra}".strip()


def _query_pool(place: PlaceInput) -> list[str]:
    """4–6 unique search lines; best_query first."""
    pool: list[str] = []
    for x in [place.best_query, *place.queries]:
        z = " ".join(str(x).split()).strip()
        if z and z.lower() not in {p.lower() for p in pool}:
            pool.append(z)
    name = place.name.strip() or "destination"
    while len(pool) < 4:
        pool.append(f"{name} iconic landmark sunset")
    return pool[:6]


def _ensure_five_places(raw: list[dict]) -> list[PlaceInput]:
    out: list[PlaceInput] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            out.append(PlaceInput.model_validate(row))
        except Exception as e:
            logger.debug("Skip invalid place row: %s", e)
    while len(out) < 5:
        n = len(out) + 1
        out.append(
            PlaceInput(
                name=f"Destination {n}",
                type="region",
                highlights=["scenic views", "local culture", "landmarks"],
                best_query=f"beautiful travel destination {n} aerial coastline",
                caption_text="You won't believe how this place feels at golden hour.",
                queries=[
                    f"destination {n} historic architecture",
                    f"destination {n} sunset waterfront",
                    f"destination {n} old town streets",
                    f"destination {n} landmark drone",
                ],
            )
        )
    return out[:5]


def _build_search_tasks(
    places: list[PlaceInput],
    max_calls: int,
) -> list[tuple[int, str, MediaKind]]:
    """Priority: each place best_query image+video, then next query index for all places, etc."""
    pools = [_query_pool(p) for p in places]
    tasks: list[tuple[int, str, MediaKind]] = []

    def append_pair(place_idx: int, q: str) -> None:
        if len(tasks) >= max_calls:
            return
        tasks.append((place_idx, q, "image"))
        if len(tasks) >= max_calls:
            return
        tasks.append((place_idx, q, "video"))

    # Depth 0: best query (index 0) for every place
    for i, pool in enumerate(pools):
        if len(tasks) >= max_calls:
            break
        append_pair(i, pool[0])

    # Further depths: round-robin additional query strings
    depth = 1
    while len(tasks) < max_calls and depth < 6:
        added = False
        for i, pool in enumerate(pools):
            if len(tasks) >= max_calls:
                break
            if depth < len(pool):
                append_pair(i, pool[depth])
                added = True
        if not added:
            break
        depth += 1

    return tasks[:max_calls]


def _merge_results(
    places: list[PlaceInput],
    task_results: list[tuple[int, list[MediaRecord]]],
    per_place_cap: int,
) -> list[PlaceWithMedia]:
    buckets: list[list[MediaRecord]] = [[] for _ in places]
    for place_idx, recs in task_results:
        if 0 <= place_idx < len(buckets):
            buckets[place_idx].extend(recs)

    out: list[PlaceWithMedia] = []
    for place, bucket in zip(places, buckets):
        seen: set[str] = set()
        merged: list[MediaRecord] = []
        bucket.sort(key=lambda m: m.score, reverse=True)
        for m in bucket:
            if m.url in seen:
                continue
            seen.add(m.url)
            merged.append(m)
            if len(merged) >= per_place_cap:
                break
        media_items = [
            MediaItem(
                type=m.type,
                url=m.url,
                photographer=m.photographer,
                width=m.width,
                height=m.height,
                score=m.score,
                tags=_infer_tags(m.url, place),
            )
            for m in merged
        ]
        pool = _query_pool(place)
        out.append(
            PlaceWithMedia(
                name=place.name,
                type=place.type,
                highlights=place.highlights,
                best_query=place.best_query,
                caption_text=place.caption_text,
                queries=[q for q in pool if q != pool[0]] if pool else place.queries,
                media=media_items,
            )
        )
    return out


def _base_title(place_name: str) -> str:
    """Strip scene suffix after ' — ' so title is just the location."""
    return place_name.split(" — ")[0].strip() if " — " in place_name else place_name


def _select_best_clips(places: list[PlaceWithMedia]) -> list[SelectedClip]:
    """Best video + best image per place (up to 2 per place, 10 total for 5 places).
    Video is listed first so n8n assembles video→image alternating per destination."""
    clips: list[SelectedClip] = []
    for place in places:
        if not place.media:
            continue
        title = _base_title(place.name)
        videos = [m for m in place.media if m.type == "video"]
        images = [m for m in place.media if m.type == "image"]
        for chosen in filter(None, [videos[0] if videos else None, images[0] if images else None]):
            clips.append(
                SelectedClip(
                    place_name=place.name,
                    title=title,
                    url=chosen.url,
                    type=chosen.type,
                    score=chosen.score,
                    width=chosen.width,
                    height=chosen.height,
                    best_query=place.best_query,
                    caption_text=place.caption_text,
                )
            )
    return clips


def _infer_tags(_url: str, place: PlaceInput) -> list[str]:
    blob = " ".join(
        [place.name, place.type, *place.highlights, place.best_query]
    ).lower()
    tags: list[str] = []
    for t in ("beach", "coast", "city", "architecture", "mountain", "night", "sunset", "historic"):
        if t in blob and t not in tags:
            tags.append(t)
    return tags[:5]


async def _download_if_requested(
    client: httpx.AsyncClient,
    items: list[PlaceWithMedia],
    root: Path,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(6)

    async def one(url: str, dest: Path) -> None:
        async with sem:
            r = await client.get(url)
            r.raise_for_status()
            dest.write_bytes(r.content)

    tasks: list[asyncio.Task[None]] = []
    for pi, place in enumerate(items):
        for mi, m in enumerate(place.media):
            h = hashlib.sha256(m.url.encode()).hexdigest()[:16]
            ext = ".mp4" if m.type == "video" else ".jpg"
            dest = root / f"p{pi:02d}_{mi:02d}_{h}{ext}"
            tasks.append(asyncio.create_task(one(m.url, dest)))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for pi, place in enumerate(items):
        new_media: list[MediaItem] = []
        for mi, m in enumerate(place.media):
            h = hashlib.sha256(m.url.encode()).hexdigest()[:16]
            ext = ".mp4" if m.type == "video" else ".jpg"
            dest = root / f"p{pi:02d}_{mi:02d}_{h}{ext}"
            lp = str(dest.resolve()) if dest.is_file() else None
            new_media.append(m.model_copy(update={"local_path": lp}))
        items[pi] = place.model_copy(update={"media": new_media})


async def aggregate_travel_media(
    user_query: str,
    client: httpx.AsyncClient,
    *,
    extra_tags: list[str] | None = None,
    orientation: str | None = None,
    download: bool = False,
) -> TravelMediaResponse:
    tags = list(extra_tags or [])
    orient = orientation or config.DEFAULT_ORIENTATION

    raw = await generate_places(user_query, client=client)
    raw_places = raw.get("places")
    if not isinstance(raw_places, list):
        raw_places = []

    places = _ensure_five_places([x for x in raw_places if isinstance(x, dict)])
    # Validate full payload shape (len 5 guaranteed)
    GroqPlacesResponse(places=places)

    max_calls = max(1, config.MAX_PEXELS_CALLS)
    task_specs = _build_search_tasks(places, max_calls)

    async def run_one(
        spec: tuple[int, str, MediaKind],
    ) -> tuple[int, list[MediaRecord], bool]:
        place_idx, q, mt = spec
        aq = _augment_query(q, tags)
        recs, cached = await search_media(client, aq, mt, orientation=orient)
        return place_idx, recs, cached

    results = await asyncio.gather(
        *[run_one(s) for s in task_specs],
        return_exceptions=True,
    )

    flat: list[tuple[int, list[MediaRecord]]] = []
    cache_hits = 0
    pexels_used = 0
    for spec, res in zip(task_specs, results):
        if isinstance(res, Exception):
            logger.warning("Pexels task failed: %s", res)
            continue
        pi, recs, cch = res
        flat.append((pi, recs))
        pexels_used += 1
        if cch:
            cache_hits += 1

    merged = _merge_results(places, flat, config.MEDIA_PER_PLACE_MAX)

    if download:
        dl_root = Path(config.OUTPUT_DIR).resolve()
        dl_root.mkdir(parents=True, exist_ok=True)
        await _download_if_requested(client, merged, dl_root)

    groq_places_struct = [
        GroqPlaceStructured(
            name=p.name,
            type=p.type,
            highlights=list(p.highlights),
            best_query=p.best_query,
            caption_text=p.caption_text,
            queries=list(p.queries),
            query_pool=_query_pool(p),
        )
        for p in places
    ]

    search_plan = [
        PexelsSearchPlanItem(
            step_index=i,
            place_index=pi,
            place_name=places[pi].name,
            base_query=q,
            pexels_query=_augment_query(q, tags),
            media_type=mt,
        )
        for i, (pi, q, mt) in enumerate(task_specs)
    ]

    return TravelMediaResponse(
        places=merged,
        selected_clips=_select_best_clips(merged),
        groq_model=config.GROQ_MODEL,
        pexels_calls_used=pexels_used,
        cache_hits=cache_hits,
        user_query=user_query.strip(),
        tags_applied=tags,
        groq_places=groq_places_struct,
        search_plan=search_plan,
    )
