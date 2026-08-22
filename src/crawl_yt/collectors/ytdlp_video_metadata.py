"""Full yt-dlp metadata provider that never downloads media."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yt_dlp import YoutubeDL

from .video_metadata import VideoMetadata


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _published_at(entry: dict[str, Any]) -> datetime | None:
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
    return None


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def normalize_metadata(entry: dict[str, Any]) -> VideoMetadata:
    video_id = str(entry.get("id") or "").strip()
    if not video_id:
        raise ValueError("yt-dlp metadata has no video ID")
    thumbnail_url = entry.get("thumbnail")
    if not thumbnail_url:
        thumbnails = entry.get("thumbnails") or []
        thumbnail_url = next(
            (item.get("url") for item in reversed(thumbnails) if item.get("url")),
            None,
        )
    return VideoMetadata(
        video_id=video_id,
        source="yt-dlp:video-full",
        channel_id=entry.get("channel_id"),
        title=entry.get("title"),
        description=entry.get("description"),
        published_at=_published_at(entry),
        duration_seconds=_integer(entry.get("duration")),
        view_count=_integer(entry.get("view_count")),
        like_count=_integer(entry.get("like_count")),
        comment_count=_integer(entry.get("comment_count")),
        thumbnail_url=thumbnail_url,
        webpage_url=entry.get("webpage_url") or entry.get("original_url"),
        availability=entry.get("availability"),
        tags=_strings(entry.get("tags")),
        categories=_strings(entry.get("categories")),
        language=entry.get("language"),
    )


class YtDlpVideoMetadataProvider:
    def fetch(
        self, video_id: str, webpage_url: str | None = None
    ) -> VideoMetadata:
        options = {
            "noplaylist": True,
            "no_warnings": False,
            "quiet": True,
            "skip_download": True,
        }
        url = webpage_url or f"https://www.youtube.com/watch?v={video_id}"
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise RuntimeError("yt-dlp returned no metadata")
        return normalize_metadata(info)
