"""Single-request yt-dlp channel metadata provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yt_dlp import YoutubeDL

from .channel_metadata import ChannelMetadata


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_channel_metadata(entry: dict[str, Any], channel_id: str | None = None) -> ChannelMetadata:
    resolved_id = str(entry.get("channel_id") or entry.get("id") or channel_id or "").strip()
    if not resolved_id:
        raise ValueError("yt-dlp channel metadata has no channel ID")
    url = entry.get("channel_url") or entry.get("uploader_url")
    if url and str(url).startswith("/"):
        url = f"https://www.youtube.com{url}"
    return ChannelMetadata(
        channel_id=resolved_id,
        title=str(entry.get("channel") or entry.get("uploader") or entry.get("title") or "").strip() or None,
        description=entry.get("description") or None,
        channel_url=str(url).rstrip("/") if url else None,
        subscriber_count=_integer(entry["subscriber_count"] if entry.get("subscriber_count") is not None else entry.get("channel_follower_count")),
        view_count=_integer(entry.get("view_count")),
        video_count=_integer(entry.get("video_count")),
        checked_at=datetime.now(timezone.utc),
    )


class YtDlpChannelMetadataProvider:
    def fetch(self, channel_id: str) -> ChannelMetadata:
        options = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": False,
            "skip_download": True,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/channel/{channel_id}/about", download=False)
        if not info:
            raise RuntimeError("yt-dlp returned no channel metadata")
        return normalize_channel_metadata(info, channel_id)
