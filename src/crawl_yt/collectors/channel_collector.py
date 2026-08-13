"""Streaming channel video enumeration and crawl orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from time import perf_counter
from typing import Iterable, Protocol

from ..database.models import Video
from ..database.repository import VideoRepository
from ..discovery.channel_scoring import CrawlPriorityPolicy


class ChannelVideoProvider(Protocol):
    def iterate_videos(
        self, channel_id: str, limit: int | None = None
    ) -> Iterable[Video | None]: ...


@dataclass(slots=True)
class CrawlReport:
    channel_id: str
    channel_title: str
    mode: str
    enumerated_entries: int = 0
    unique_videos: int = 0
    new_videos: int = 0
    existing_videos: int = 0
    skipped_entries: int = 0
    stopped_early: bool = False
    stop_reason: str | None = None
    consecutive_known_at_stop: int = 0
    elapsed_seconds: float = 0.0


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
        self,
        provider: ChannelVideoProvider,
        repository: VideoRepository,
        crawl_interval: timedelta = timedelta(hours=24),
        priority_policy: CrawlPriorityPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.crawl_interval = crawl_interval
        self.priority_policy = priority_policy or CrawlPriorityPolicy()

    def crawl(
        self,
        channel_id: str,
        limit: int | None = None,
        *,
        full: bool = False,
        known_stop_threshold: int = 5,
    ) -> CrawlReport:
        channel = self.repository.get_channel(channel_id)
        if channel is None:
            raise UnknownChannelError(
                f"Channel {channel_id} is not in the database; discover it first"
            )
        if known_stop_threshold < 1:
            raise ValueError("known_stop_threshold must be positive")
        state = self.repository.get_channel_crawl_state(channel_id)
        incremental = not full and state is not None and state.total_crawls > 0
        report = CrawlReport(channel_id, channel.title, "incremental" if incremental else "full")
        started = perf_counter()
        self.repository.mark_crawl_started(channel_id)
        seen_ids: set[str] = set()
        consecutive_known = 0
        last_seen: Video | None = None
        try:
            # YouTube uploads are assumed newest-first; early stop is unsafe otherwise.
            for video in self.provider.iterate_videos(channel_id, limit):
                report.enumerated_entries += 1
                if video is None or video.video_id in seen_ids:
                    report.skipped_entries += 1
                    continue
                seen_ids.add(video.video_id)
                report.unique_videos += 1
                if last_seen is None:
                    last_seen = video
                known = self.repository.video_exists(video.video_id)
                self.repository.upsert_video(video)
                if known:
                    report.existing_videos += 1
                    consecutive_known += 1
                else:
                    report.new_videos += 1
                    consecutive_known = 0
                if incremental and consecutive_known >= known_stop_threshold:
                    report.stopped_early = True
                    report.stop_reason = (
                        f"{known_stop_threshold} consecutive known videos"
                    )
                    report.consecutive_known_at_stop = consecutive_known
                    break
        except Exception as error:
            self.repository.mark_crawl_failure(
                channel_id, str(error), crawl_interval=self.crawl_interval
            )
            raise
        channel_score = self.repository.get_channel_score(channel_id)
        interval = self.priority_policy.interval_for(
            channel_score.tier if channel_score else None
        )
        self.repository.mark_crawl_success(
            channel_id,
            last_seen.video_id if last_seen else None,
            last_seen.published_at if last_seen else None,
            crawl_interval=interval,
        )
        report.elapsed_seconds = perf_counter() - started
        return report

    def crawl_all(
        self,
        max_channels: int | None = None,
        limit_per_channel: int | None = None,
    ) -> CrawlAllReport:
        return self._crawl_channels(
            self.repository.list_channels(limit=max_channels), limit_per_channel
        )

    def crawl_due(self, limit: int) -> CrawlAllReport:
        return self._crawl_channels(
            self.repository.list_channels_due_for_crawl(limit=limit), None
        )

    def _crawl_channels(
        self, channels: Iterable, limit_per_channel: int | None
    ) -> CrawlAllReport:
        aggregate = CrawlAllReport()
        for channel in channels:
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
