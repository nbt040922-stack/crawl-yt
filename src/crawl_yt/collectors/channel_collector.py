"""Channel video provider boundary and crawl orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..database.models import Video
from ..database.repository import VideoRepository


@dataclass(slots=True)
class VideoBatch:
    enumerated_entries: int
    videos: list[Video]
    skipped_entries: int = 0


class ChannelVideoProvider(Protocol):
    def list_videos(self, channel_id: str, limit: int | None = None) -> VideoBatch: ...


@dataclass(slots=True)
class CrawlReport:
    channel_id: str
    channel_title: str
    enumerated_entries: int
    unique_videos: int
    new_videos: int
    existing_videos: int
    skipped_entries: int


@dataclass(slots=True)
class CrawlAllReport:
    channels_attempted: int = 0
    channels_succeeded: int = 0
    enumerated_entries: int = 0
    unique_videos: int = 0
    new_videos: int = 0
    existing_videos: int = 0
    skipped_entries: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


class UnknownChannelError(ValueError):
    pass


class ChannelCrawlService:
    def __init__(
        self, provider: ChannelVideoProvider, repository: VideoRepository
    ) -> None:
        self.provider = provider
        self.repository = repository

    def crawl(self, channel_id: str, limit: int | None = None) -> CrawlReport:
        channel = self.repository.get_channel(channel_id)
        if channel is None:
            raise UnknownChannelError(
                f"Channel {channel_id} is not in the database; discover it first"
            )
        batch = self.provider.list_videos(channel_id, limit)
        unique = {video.video_id: video for video in batch.videos}
        new_videos = 0
        for video in unique.values():
            new_videos += self.repository.upsert_video(video)
        return CrawlReport(
            channel_id=channel_id,
            channel_title=channel.title,
            enumerated_entries=batch.enumerated_entries,
            unique_videos=len(unique),
            new_videos=new_videos,
            existing_videos=len(unique) - new_videos,
            skipped_entries=(
                batch.skipped_entries + len(batch.videos) - len(unique)
            ),
        )

    def crawl_all(
        self,
        max_channels: int | None = None,
        limit_per_channel: int | None = None,
    ) -> CrawlAllReport:
        aggregate = CrawlAllReport()
        for channel in self.repository.list_channels(limit=max_channels):
            aggregate.channels_attempted += 1
            try:
                report = self.crawl(channel.channel_id, limit_per_channel)
            except Exception as error:
                aggregate.failures.append((channel.channel_id, str(error)))
                continue
            aggregate.channels_succeeded += 1
            aggregate.enumerated_entries += report.enumerated_entries
            aggregate.unique_videos += report.unique_videos
            aggregate.new_videos += report.new_videos
            aggregate.existing_videos += report.existing_videos
            aggregate.skipped_entries += report.skipped_entries
        return aggregate
