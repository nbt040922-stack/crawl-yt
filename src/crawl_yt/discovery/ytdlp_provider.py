"""yt-dlp-backed YouTube search provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yt_dlp import YoutubeDL

from ..database.models import Channel
from .channel_discovery import ChannelVerification, DiscoveryBatch


def channel_videos_url(channel: Channel) -> str:
    """Return the videos tab URL so yt-dlp yields video entries, not tab links."""
    base = channel.channel_url or f"https://www.youtube.com/channel/{channel.channel_id}"
    return base.rstrip("/") + "/videos"


def normalize_channel(entry: dict[str, Any]) -> Channel | None:
    channel_id = entry.get("channel_id")
    if not channel_id:
        uploader_id = entry.get("uploader_id")
        channel_id = uploader_id if str(uploader_id or "").startswith("UC") else None
    if not str(channel_id or "").startswith("UC"):
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
        last_checked_at=now,
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
            if (channel := normalize_channel(entry)) is not None
        ]
        return DiscoveryBatch(
            search_results=len(entries),
            channels=channels,
            source="yt-dlp:ytsearch",
        )

    def verify(self, channel: Channel, sample_size: int = 20) -> ChannelVerification:
        url = channel_videos_url(channel)
        options = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": False,
            "skip_download": True,
            "playlistend": min(sample_size, 20),
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = [entry for entry in (info or {}).get("entries", []) if entry]
        verified = Channel(
            channel_id=channel.channel_id,
            title=str((info or {}).get("title") or channel.title),
            description=(info or {}).get("description") or channel.description,
            channel_url=channel.channel_url or f"https://www.youtube.com/channel/{channel.channel_id}",
            subscriber_count=channel.subscriber_count,
            video_count=channel.video_count,
            view_count=channel.view_count,
            last_checked_at=channel.last_checked_at,
        )
        return ChannelVerification(verified, [str(entry.get("title") or "") for entry in entries])
