"""yt-dlp-backed YouTube search provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yt_dlp import YoutubeDL

from ..database.models import Channel
from .channel_discovery import DiscoveryBatch


def normalize_channel(entry: dict[str, Any], keyword: str) -> Channel | None:
    channel_id = entry.get("channel_id") or entry.get("uploader_id")
    if not channel_id:
        return None
    channel_id = str(channel_id)
    title = entry.get("channel") or entry.get("uploader") or channel_id
    url = entry.get("channel_url") or entry.get("uploader_url")
    if url and str(url).startswith("/"):
        url = f"https://www.youtube.com{url}"
    elif not url and channel_id.startswith("UC"):
        url = f"https://www.youtube.com/channel/{channel_id}"
    if url:
        url = str(url).rstrip("/")
    now = datetime.now(timezone.utc)
    return Channel(
        channel_id=channel_id,
        title=str(title),
        channel_url=url,
        subscriber_count=entry.get("channel_follower_count"),
        discovery_keyword=keyword,
        discovered_at=now,
        last_checked_at=now,
        discovery_source="yt-dlp:ytsearch",
    )


class YtDlpDiscoveryProvider:
    def search(self, keyword: str, limit: int) -> DiscoveryBatch:
        options = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": False,
            "skip_download": True,
            "playlistend": limit,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{keyword}", download=False)
        entries = [entry for entry in (info or {}).get("entries", []) if entry]
        channels = [
            channel
            for entry in entries
            if (channel := normalize_channel(entry, keyword)) is not None
        ]
        return DiscoveryBatch(search_results=len(entries), channels=channels)
