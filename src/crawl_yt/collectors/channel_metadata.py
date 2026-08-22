"""Channel-level metadata contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ChannelMetadata:
    channel_id: str
    title: str | None = None
    description: str | None = None
    channel_url: str | None = None
    subscriber_count: int | None = None
    view_count: int | None = None
    video_count: int | None = None
    checked_at: datetime | None = None


class ChannelMetadataProvider:
    def fetch(self, channel_id: str) -> ChannelMetadata:  # pragma: no cover - protocol shape
        raise NotImplementedError
