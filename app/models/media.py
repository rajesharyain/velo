from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MediaRecord(BaseModel):
    """Raw-ish record from Pexels before response shaping."""

    type: Literal["image", "video"]
    url: str
    photographer: str | None = None
    width: int | None = None
    height: int | None = None
    score: float = 0.0


class MediaItem(BaseModel):
    """Public API media item."""

    type: Literal["image", "video"]
    url: str
    photographer: str | None = None
    width: int | None = None
    height: int | None = None
    tags: list[str] = Field(default_factory=list)
    local_path: str | None = None
