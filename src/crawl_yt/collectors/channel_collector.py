"""Streaming channel video enumeration and crawl orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Iterable, Protocol

from ..database.models import Video
from ..database.repository import VideoRepository
from ..discovery.channel_scoring import CrawlPriorityPolicy
from ..scoring_lifecycle import ChannelScoringLifecycle

MAX_CADENCE_METADATA_FETCHES = 150


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
    scoring_error: str | None = None
    cadence_metadata_attempted: int = 0
    cadence_metadata_succeeded: int = 0
    cadence_metadata_failed: int = 0
    cadence_metadata_error: str | None = None


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
        crawl_interval: timedelta | None = None,
        priority_policy: CrawlPriorityPolicy | None = None,
        scoring_lifecycle: ChannelScoringLifecycle | None = None,
        cadence_metadata_provider=None,
        max_cadence_metadata_fetches: int = MAX_CADENCE_METADATA_FETCHES,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.crawl_interval = crawl_interval
        self.priority_policy = priority_policy or CrawlPriorityPolicy()
        self.scoring_lifecycle = scoring_lifecycle or ChannelScoringLifecycle(repository)
        self.cadence_metadata_provider = cadence_metadata_provider
        self.max_cadence_metadata_fetches = max_cadence_metadata_fetches

    def crawl(
        self,
        channel_id: str,
        limit: int | None = None,
        *,
        full: bool = False,
        known_stop_threshold: int = 5,
        now: datetime | None = None,
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
        crawl_now = now or datetime.now(timezone.utc)
        self.repository.mark_crawl_started(channel_id, now=crawl_now)
        seen_ids: set[str] = set()
        seen_order: list[str] = []
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
                seen_order.append(video.video_id)
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
                channel_id, str(error), now=crawl_now, crawl_interval=self.crawl_interval
            )
            raise
        if full and self.cadence_metadata_provider is not None:
            self._collect_cadence_metadata(
                channel_id, seen_order, crawl_now, report
            )
        previous_score = self.repository.get_channel_score(channel_id)
        tier = previous_score.tier if previous_score else None
        try:
            rescored = self.scoring_lifecycle.score_channel(channel_id)
            tier = getattr(rescored, "tier", None) or (
                self.repository.get_channel_score(channel_id).tier
                if self.repository.get_channel_score(channel_id)
                else tier
            )
        except Exception as error:
            report.scoring_error = str(error)
        interval = self.priority_policy.interval_for(tier)
        self.repository.mark_crawl_success(
            channel_id,
            last_seen.video_id if last_seen else None,
            last_seen.published_at if last_seen else None,
            now=crawl_now,
            crawl_interval=interval,
        )
        report.elapsed_seconds = perf_counter() - started
        return report

    def _collect_cadence_metadata(
        self, channel_id: str, video_ids: list[str], now: datetime, report: CrawlReport
    ) -> None:
        """Fetch bounded recent metadata until a dated 90-day boundary is known."""
        from .video_metadata import VideoMetadataService

        if self.max_cadence_metadata_fetches < 1:
            return
        cutoff = now - timedelta(days=90)
        service = VideoMetadataService(
            self.cadence_metadata_provider, self.repository, scoring_lifecycle=self.scoring_lifecycle
        )
        for video_id in video_ids:
            stored = self.repository.get_video(video_id)
            if stored is None:
                continue
            dated = stored.published_at
            if dated is not None and dated <= cutoff:
                break
            if dated is None:
                if report.cadence_metadata_attempted >= self.max_cadence_metadata_fetches:
                    break
                report.cadence_metadata_attempted += 1
                result = service.enrich(video_id, rescore=False)
                if result.success:
                    report.cadence_metadata_succeeded += 1
                else:
                    report.cadence_metadata_failed += 1
                    report.cadence_metadata_error = result.error
                    continue
                stored = self.repository.get_video(video_id)
                dated = stored.published_at if stored is not None else None
            if dated is not None and dated <= cutoff:
                break

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
