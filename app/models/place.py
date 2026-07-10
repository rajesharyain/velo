from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.media import MediaItem


class PlaceInput(BaseModel):
    """Single place as returned by Groq (before media fetch)."""

    model_config = {"extra": "ignore"}

    name: str
    type: str = "city"
    highlights: list[str] = Field(default_factory=list)
    best_query: str
    queries: list[str] = Field(default_factory=list)
    caption_text: str = Field(
        default="",
        max_length=420,
        description="Short on-reel blurb: why the place is famous / what it is.",
    )

    @field_validator("name", "best_query")
    @classmethod
    def strip_nonempty(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("must be non-empty")
        return s

    @field_validator("queries", "highlights", mode="before")
    @classmethod
    def list_of_str(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("caption_text", mode="before")
    @classmethod
    def caption_text_clean(cls, v: object) -> str:
        if v is None:
            return ""
        s = " ".join(str(v).split()).strip()
        words = s.split()
        if len(words) > 15:
            s = " ".join(words[:15])
        return s[:420]


class GroqPlacesResponse(BaseModel):
    places: list[PlaceInput] = Field(min_length=5, max_length=5)


class PlaceWithMedia(BaseModel):
    name: str
    type: str
    highlights: list[str] = Field(default_factory=list)
    best_query: str
    queries: list[str] = Field(default_factory=list)
    caption_text: str = Field(default="", max_length=420)
    media: list[MediaItem] = Field(default_factory=list)


class GroqPlaceStructured(BaseModel):
    """Groq-normalized place row (before Pexels), for UI / debugging."""

    name: str
    type: str
    highlights: list[str] = Field(default_factory=list)
    best_query: str
    caption_text: str = Field(default="", max_length=420)
    queries: list[str] = Field(
        default_factory=list,
        description="Query strings from the model (excluding best_query).",
    )
    query_pool: list[str] = Field(
        default_factory=list,
        description="Ordered pool used to build Pexels tasks (deduped, min length enforced).",
    )


class PexelsSearchPlanItem(BaseModel):
    """One scheduled Pexels HTTP search (image or video)."""

    step_index: int
    place_index: int
    place_name: str
    base_query: str
    pexels_query: str
    media_type: Literal["image", "video"]


class TravelMediaRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    """Optional extra tags appended to each search (e.g. beach, architecture)."""
    tags: list[str] = Field(default_factory=list, max_length=30)
    download: bool = False
    orientation: str | None = Field(
        default=None,
        description="Pexels orientation: landscape, portrait, or square. Default from env.",
    )


class TravelMediaResponse(BaseModel):
    places: list[PlaceWithMedia]
    groq_model: str | None = None
    pexels_calls_used: int = 0
    cache_hits: int = 0
    user_query: str = ""
    tags_applied: list[str] = Field(default_factory=list)
    groq_places: list[GroqPlaceStructured] = Field(default_factory=list)
    search_plan: list[PexelsSearchPlanItem] = Field(default_factory=list)
