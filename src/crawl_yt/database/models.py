"""Storage-neutral data structures for core entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Channel:
    channel_id: str
    title: str
    description: str | None = None
    channel_url: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    discovery_keyword: str | None = None
    discovered_at: datetime | None = None
    last_checked_at: datetime | None = None
    discovery_source: str | None = None


@dataclass(slots=True)
class Video:
    video_id: str
    channel_id: str
    title: str
    description: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    thumbnail_url: str | None = None


@dataclass(slots=True)
class Transcript:
    video_id: str
    language: str
    source: str
    text: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
