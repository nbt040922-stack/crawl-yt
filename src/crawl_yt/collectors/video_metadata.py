"""Selective video metadata enrichment boundary and service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, TypeVar

from ..database.models import Video
from ..database.repository import VideoRepository

T = TypeVar("T")


@dataclass(slots=True)
class VideoMetadata:
    video_id: str
    source: str
    channel_id: str | None = None
    title: str | None = None
    description: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    thumbnail_url: str | None = None
    webpage_url: str | None = None
    availability: str | None = None
    tags: list[str] | None = None
    categories: list[str] | None = None
    language: str | None = None


class VideoMetadataProvider(Protocol):
    def fetch(self, video_id: str, webpage_url: str | None = None) -> VideoMetadata: ...


@dataclass(slots=True)
class EnrichmentResult:
    video_id: str
    success: bool
    error: str | None = None
    channel_mismatch: bool = False


@dataclass(slots=True)
class EnrichmentBatchReport:
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[EnrichmentResult] = field(default_factory=list)


class VideoMetadataService:
    def __init__(
        self, provider: VideoMetadataProvider, repository: VideoRepository
    ) -> None:
        self.provider = provider
        self.repository = repository

    def enrich(self, video_id: str) -> EnrichmentResult:
        stored = self.repository.get_video(video_id)
        if stored is None:
            return EnrichmentResult(video_id, False, "video is not in the database")
        try:
            metadata = self.provider.fetch(video_id, stored.webpage_url)
        except Exception as error:
            return EnrichmentResult(video_id, False, str(error))
        if metadata.video_id != stored.video_id:
            return EnrichmentResult(
                video_id, False, f"provider returned video {metadata.video_id}"
            )
        if metadata.channel_id and metadata.channel_id != stored.channel_id:
            return EnrichmentResult(
                video_id,
                False,
                f"channel mismatch: stored={stored.channel_id}, provider={metadata.channel_id}",
                channel_mismatch=True,
            )

        now = datetime.now(timezone.utc)
        enriched = Video(
            video_id=stored.video_id,
            channel_id=stored.channel_id,
            title=metadata.title or stored.title,
            first_seen_at=stored.first_seen_at,
            description=self._prefer(metadata.description, stored.description),
            published_at=self._prefer(metadata.published_at, stored.published_at),
            duration_seconds=self._prefer(
                metadata.duration_seconds, stored.duration_seconds
            ),
            view_count=self._prefer(metadata.view_count, stored.view_count),
            like_count=self._prefer(metadata.like_count, stored.like_count),
            comment_count=self._prefer(metadata.comment_count, stored.comment_count),
            thumbnail_url=self._prefer(metadata.thumbnail_url, stored.thumbnail_url),
            webpage_url=self._prefer(metadata.webpage_url, stored.webpage_url),
            availability=self._prefer(metadata.availability, stored.availability),
            last_checked_at=now,
            metadata_source=metadata.source,
            tags=self._prefer(metadata.tags, stored.tags),
            categories=self._prefer(metadata.categories, stored.categories),
            language=self._prefer(metadata.language, stored.language),
            metadata_enriched_at=now,
        )
        self.repository.upsert_video(enriched)
        return EnrichmentResult(video_id, True)

    def enrich_channel(self, channel_id: str, limit: int) -> EnrichmentBatchReport:
        if self.repository.get_channel(channel_id) is None:
            raise ValueError(f"channel {channel_id} is not in the database")
        return self._batch(
            self.repository.list_videos_needing_enrichment(channel_id, limit)
        )

    def enrich_pending(self, limit: int) -> EnrichmentBatchReport:
        return self._batch(self.repository.list_videos_needing_enrichment(limit=limit))

    def _batch(self, videos: list[Video]) -> EnrichmentBatchReport:
        report = EnrichmentBatchReport()
        for video in videos:
            result = self.enrich(video.video_id)
            report.attempted += 1
            report.succeeded += result.success
            report.failed += not result.success
            report.results.append(result)
        return report

    @staticmethod
    def _prefer(new: T | None, old: T | None) -> T | None:
        return old if new is None else new
