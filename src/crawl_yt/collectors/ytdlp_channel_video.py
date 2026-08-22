"""Flat yt-dlp provider for efficient channel upload enumeration."""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Iterator
from typing import Any

from yt_dlp import YoutubeDL

from ..database.models import Video


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def published_at_from_entry(entry: dict[str, Any]) -> datetime | None:
    timestamp = entry.get("timestamp")
    if timestamp is None:
        timestamp = entry.get("release_timestamp")
    if timestamp is not None:
        try:
            return datetime.fromtimestamp(float(timestamp), timezone.utc)
        except (OSError, TypeError, ValueError):
            pass
    upload_date = str(entry.get("upload_date") or "")
    if len(upload_date) == 8 and upload_date.isdigit():
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
    release_date = str(entry.get("release_date") or "")
    if len(release_date) == 8 and release_date.isdigit():
        return datetime.strptime(release_date, "%Y%m%d").replace(tzinfo=timezone.utc)
    return None


def normalize_video(entry: dict[str, Any], channel_id: str) -> Video | None:
    video_id = str(entry.get("id") or "").strip()
    if not video_id:
        return None
    webpage_url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
    if not str(webpage_url or "").startswith(("http://", "https://")):
        webpage_url = f"https://www.youtube.com/watch?v={video_id}"
    thumbnail_url = entry.get("thumbnail")
    if not thumbnail_url:
        thumbnails = entry.get("thumbnails") or []
        thumbnail_url = next(
            (item.get("url") for item in reversed(thumbnails) if item.get("url")),
            None,
        )
    now = datetime.now(timezone.utc)
    return Video(
        video_id=video_id,
        channel_id=channel_id,
        title=str(entry.get("title") or video_id),
        first_seen_at=now,
        description=entry.get("description"),
        published_at=published_at_from_entry(entry),
        duration_seconds=_integer(entry.get("duration")),
        view_count=_integer(entry.get("view_count")),
        like_count=_integer(entry.get("like_count")),
        comment_count=_integer(entry.get("comment_count")),
        thumbnail_url=thumbnail_url,
        webpage_url=str(webpage_url),
        availability=entry.get("availability"),
        last_checked_at=now,
        metadata_source="yt-dlp:channel-flat",
    )


class YtDlpChannelVideoProvider:
    def iterate_videos(
        self, channel_id: str, limit: int | None = None
    ) -> Iterator[Video | None]:
        options: dict[str, Any] = {
            "extract_flat": "in_playlist",
            "ignoreerrors": False,
            "lazy_playlist": True,
            "no_warnings": False,
            "quiet": True,
            "skip_download": True,
        }
        if limit is not None:
            options["playlistend"] = limit
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise RuntimeError("yt-dlp returned no channel data")
            for entry in info.get("entries", []):
                yield normalize_video(entry, channel_id) if entry else None
