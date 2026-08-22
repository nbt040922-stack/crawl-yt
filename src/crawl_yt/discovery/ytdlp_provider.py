"""yt-dlp-backed YouTube search provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from yt_dlp import YoutubeDL

from ..database.models import Channel
from ..collectors.ytdlp_channel_video import normalize_video, published_at_from_entry
from .cadence import (
    CADENCE_PROBE_MAX_DATE_ENRICHMENTS,
    CADENCE_PROBE_MAX_ENTRIES,
    CadenceProbe,
)
from .activity import DiscoverySearchResult
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
        channels = []
        results = []
        for entry in entries:
            channel = normalize_channel(entry)
            if channel is None:
                continue
            channels.append(channel)
            video = normalize_video(entry, channel.channel_id)
            results.append(DiscoverySearchResult(
                channel,
                video.video_id if video else str(entry.get("id") or "") or None,
                video.published_at if video else published_at_from_entry(entry),
            ))
        return DiscoveryBatch(
            search_results=len(entries),
            channels=channels,
            source="yt-dlp:ytsearch",
            results=results,
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
        recent_videos = [
            video
            for entry in entries
            if (video := normalize_video(entry, channel.channel_id)) is not None
        ]
        return ChannelVerification(
            verified,
            [str(entry.get("title") or "") for entry in entries],
            recent_videos,
        )

    def probe_cadence(
        self,
        channel: Channel,
        max_entries: int = CADENCE_PROBE_MAX_ENTRIES,
        max_date_enrichments: int = CADENCE_PROBE_MAX_DATE_ENRICHMENTS,
        known_videos=(),
    ) -> CadenceProbe:
        options = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": False,
            "skip_download": True,
            "playlistend": min(max_entries, CADENCE_PROBE_MAX_ENTRIES),
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(channel_videos_url(channel), download=False)
        raw_entries = [entry for entry in (info or {}).get("entries", []) if entry]
        entries = []
        seen_ids = set()
        for entry in raw_entries:
            video_id = str(entry.get("id") or "").strip()
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            entries.append(entry)
        known_dates = {
            video.video_id: video.published_at
            for video in known_videos
            if video.published_at is not None
        }
        dates_by_id = {}
        for entry in entries:
            video_id = str(entry.get("id") or "").strip()
            date = published_at_from_entry(entry) or known_dates.get(video_id)
            if date is not None:
                dates_by_id[video_id] = date

        enrichments = 0
        failures = 0
        exhausted = len(raw_entries) < min(max_entries, CADENCE_PROBE_MAX_ENTRIES)
        if not self._probe_confident(entries, dates_by_id, exhausted):
            with YoutubeDL({"quiet": True, "no_warnings": False, "skip_download": True}) as ydl:
                for entry in entries:
                    video_id = str(entry.get("id") or "").strip()
                    if video_id in dates_by_id:
                        continue
                    if enrichments >= min(max_date_enrichments, CADENCE_PROBE_MAX_DATE_ENRICHMENTS):
                        break
                    enrichments += 1
                    url = entry.get("webpage_url") or entry.get("original_url") or f"https://www.youtube.com/watch?v={video_id}"
                    try:
                        metadata = ydl.extract_info(url, download=False) or {}
                        date = published_at_from_entry(metadata)
                        if date is not None:
                            dates_by_id[video_id] = date
                    except Exception:
                        failures += 1
                    if self._probe_confident(entries, dates_by_id, exhausted):
                        break
        reason = (
            "30-day boundary reached"
            if self._probe_confident(entries, dates_by_id, exhausted)
            else f"{len(dates_by_id)}/{len(entries)} publication dates resolved; enrichment limit reached before 30-day window was established"
        )
        return CadenceProbe(
            tuple(dates_by_id.values()), exhausted,
            entries_enumerated=len(entries),
            dates_available=len(dates_by_id),
            dates_enriched=enrichments,
            enrichment_failures=failures,
            confidence_reason=reason,
            confidence_reached=self._probe_confident(entries, dates_by_id, exhausted),
        )

    @staticmethod
    def _probe_confident(entries, dates_by_id, exhausted: bool) -> bool:
        dates = list(dates_by_id.values())
        if not dates:
            return False
        reference = datetime.now(timezone.utc)
        cutoff = reference - timedelta(days=30)
        recent = sum(value >= cutoff for value in dates)
        if recent >= 30:
            return True
        for entry in entries:
            video_id = str(entry.get("id") or "").strip()
            date = dates_by_id.get(video_id)
            if date is None:
                return False
            if date <= cutoff:
                return True
        return exhausted and len(dates) >= len(entries)
