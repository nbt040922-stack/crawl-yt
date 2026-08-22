"""Network-free channel crawl provider and service tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.collectors.channel_collector import (
    ChannelCrawlService,
    UnknownChannelError,
)
from src.crawl_yt.collectors.ytdlp_channel_video import normalize_video
from src.crawl_yt.collectors.video_metadata import VideoMetadata
from src.crawl_yt.collectors.channel_metadata import ChannelMetadata
from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import VideoRepository


class FakeVideoProvider:
    def __init__(self, failing_channel: str | None = None) -> None:
        self.failing_channel = failing_channel

    def iterate_videos(self, channel_id: str, limit: int | None = None):
        if channel_id == self.failing_channel:
            raise RuntimeError("provider failure")
        now = datetime.now(timezone.utc)
        videos = [
            Video(f"{channel_id}-1", channel_id, "One", now),
            Video(f"{channel_id}-2", channel_id, "Two", now),
        ]
        yield from videos[:limit]


class DatedMetadataProvider:
    def __init__(self, dates):
        self.dates = dates
        self.calls = []

    def fetch(self, video_id, webpage_url=None):
        self.calls.append(video_id)
        return VideoMetadata(video_id, "fake", published_at=self.dates[video_id])


class ChannelMetadataFake:
    def __init__(self, events):
        self.calls = []
        self.events = events

    def fetch(self, channel_id):
        self.calls.append(channel_id)
        self.events.append("channel_metadata")
        return ChannelMetadata(channel_id, title="Hydrated", subscriber_count=12345, view_count=99999, video_count=20)


class ChannelCrawlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(
            Path(self.temporary_directory.name) / "test.db"
        )
        self.repository.upsert_channel(Channel("UC123", "Example"))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_provider_result_normalization(self) -> None:
        video = normalize_video(
            {
                "id": "abc123",
                "title": "Example Video",
                "url": "abc123",
                "duration": 42.8,
                "view_count": "100",
                "upload_date": "20260812",
            },
            "UC123",
        )
        self.assertIsNotNone(video)
        self.assertEqual(video.channel_id, "UC123")
        self.assertEqual(video.duration_seconds, 42)
        self.assertEqual(video.view_count, 100)
        self.assertEqual(video.webpage_url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(video.metadata_source, "yt-dlp:channel-flat")

    def test_provider_mapping_prefers_timestamp_then_release_timestamp_then_upload_date(self) -> None:
        timestamp = normalize_video({"id": "timestamp", "timestamp": 0, "release_timestamp": 86400, "upload_date": "20260812"}, "UC123")
        release = normalize_video({"id": "release", "release_timestamp": 86400}, "UC123")
        upload = normalize_video({"id": "upload", "upload_date": "20260812"}, "UC123")
        release_date = normalize_video({"id": "release-date", "release_date": "20260812"}, "UC123")
        self.assertEqual(timestamp.published_at, datetime(1970, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(release.published_at, datetime(1970, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(upload.published_at, datetime(2026, 8, 12, tzinfo=timezone.utc))
        self.assertEqual(release_date.published_at, datetime(2026, 8, 12, tzinfo=timezone.utc))

    def test_full_crawl_bounded_metadata_dates_enable_cadence_before_rescore(self) -> None:
        base = datetime(2026, 8, 13, tzinfo=timezone.utc)
        videos = [Video(f"dated-{i}", "UC123", str(i), base) for i in range(20)]
        dates = {video.video_id: base - timedelta(days=i * 7) for i, video in enumerate(videos)}
        metadata = DatedMetadataProvider(dates)
        class Provider:
            def iterate_videos(self, channel_id, limit=None):
                yield from videos
        report = ChannelCrawlService(Provider(), self.repository, cadence_metadata_provider=metadata).crawl("UC123", full=True, now=base)
        score = self.repository.get_channel_score("UC123")
        self.assertGreater(report.cadence_metadata_succeeded, 0)
        self.assertLessEqual(report.cadence_metadata_attempted, 150)
        self.assertIsNotNone(score.videos_per_week_30d)
        self.assertIsNotNone(score.videos_per_week_90d)
        self.assertNotEqual(score.reasons["score_maturity"], "preliminary")

    def test_full_crawl_missing_dates_stays_unknown_and_respects_cap(self) -> None:
        videos = [Video(f"missing-{i}", "UC123", str(i), datetime(2026, 8, 13, tzinfo=timezone.utc)) for i in range(500)]
        class Provider:
            def iterate_videos(self, channel_id, limit=None):
                yield from videos
        class MissingMetadata:
            def __init__(self): self.calls = []
            def fetch(self, video_id, webpage_url=None):
                self.calls.append(video_id)
                return VideoMetadata(video_id, "fake")
        metadata = MissingMetadata()
        report = ChannelCrawlService(Provider(), self.repository, cadence_metadata_provider=metadata, max_cadence_metadata_fetches=150).crawl("UC123", full=True)
        score = self.repository.get_channel_score("UC123")
        self.assertEqual(len(metadata.calls), 150)
        self.assertIsNone(score.videos_per_week_90d)
        self.assertEqual(report.cadence_metadata_failed, 0)

    def test_full_crawl_hydrates_channel_once_after_cadence_and_incremental_skips(self) -> None:
        events = []
        metadata = ChannelMetadataFake(events)
        class Provider:
            def iterate_videos(self, channel_id, limit=None):
                events.append("videos")
                yield Video("ordered", channel_id, "Ordered", datetime.now(timezone.utc))
        class Lifecycle:
            def score_channel(self, channel_id):
                events.append("score")
                return None
        service = ChannelCrawlService(Provider(), self.repository, scoring_lifecycle=Lifecycle(), channel_metadata_provider=metadata)
        service.crawl("UC123", full=True)
        self.assertEqual(events, ["videos", "channel_metadata", "score"])
        self.assertEqual(metadata.calls, ["UC123"])
        service.crawl("UC123", full=False)
        self.assertEqual(metadata.calls, ["UC123"])

    def test_channel_metadata_failure_preserves_crawl_and_score(self) -> None:
        class Provider:
            def iterate_videos(self, channel_id, limit=None):
                yield Video("kept", channel_id, "Kept", datetime.now(timezone.utc))
        class BrokenMetadata:
            def fetch(self, channel_id):
                raise RuntimeError("channel metadata unavailable")
        report = ChannelCrawlService(Provider(), self.repository, channel_metadata_provider=BrokenMetadata()).crawl("UC123", full=True)
        self.assertEqual(report.channel_metadata_error, "channel metadata unavailable")
        self.assertEqual(self.repository.count_videos(), 1)
        self.assertIsNotNone(self.repository.get_channel_score("UC123"))

    def test_second_crawl_reports_existing_videos(self) -> None:
        service = ChannelCrawlService(FakeVideoProvider(), self.repository)
        first = service.crawl("UC123", limit=20)
        second = service.crawl("UC123", limit=20)
        self.assertEqual((first.new_videos, first.existing_videos), (2, 0))
        self.assertEqual((second.new_videos, second.existing_videos), (0, 2))
        self.assertEqual(self.repository.count_videos(), 2)

    def test_unknown_channel_has_clear_error(self) -> None:
        service = ChannelCrawlService(FakeVideoProvider(), self.repository)
        with self.assertRaisesRegex(UnknownChannelError, "discover it first"):
            service.crawl("UC-missing")

    def test_crawl_all_continues_after_failure(self) -> None:
        self.repository.upsert_channel(Channel("UC456", "Broken"))
        service = ChannelCrawlService(
            FakeVideoProvider(failing_channel="UC456"), self.repository
        )
        report = service.crawl_all(max_channels=2, limit_per_channel=10)
        self.assertEqual(report.channels_attempted, 2)
        self.assertEqual(report.channels_succeeded, 1)
        self.assertEqual(report.new_videos, 2)
        self.assertEqual(report.failures, [("UC456", "provider failure")])

    def test_successful_crawl_rescores_channel(self) -> None:
        class Lifecycle:
            def __init__(self): self.calls = []
            def score_channel(self, channel_id): self.calls.append(channel_id)
        lifecycle = Lifecycle()
        report = ChannelCrawlService(FakeVideoProvider(), self.repository, scoring_lifecycle=lifecycle).crawl("UC123")
        self.assertIsNone(report.scoring_error)
        self.assertEqual(lifecycle.calls, ["UC123"])

    def test_failed_crawl_does_not_rescore_channel(self) -> None:
        class Lifecycle:
            def __init__(self): self.calls = []
            def score_channel(self, channel_id): self.calls.append(channel_id)
        lifecycle = Lifecycle()
        with self.assertRaises(RuntimeError):
            ChannelCrawlService(FakeVideoProvider(failing_channel="UC123"), self.repository, scoring_lifecycle=lifecycle).crawl("UC123")
        self.assertEqual(lifecycle.calls, [])

    def test_score_failure_does_not_change_crawl_success(self) -> None:
        class Lifecycle:
            def score_channel(self, channel_id): raise RuntimeError("score failed")
        report = ChannelCrawlService(FakeVideoProvider(), self.repository, scoring_lifecycle=Lifecycle()).crawl("UC123", now=datetime(2026, 8, 13, tzinfo=timezone.utc))
        self.assertEqual(report.scoring_error, "score failed")
        state = self.repository.get_channel_crawl_state("UC123")
        self.assertEqual(state.total_crawls, 1)
        self.assertEqual(state.next_crawl_at, datetime(2026, 8, 14, tzinfo=timezone.utc))

    def test_successful_crawl_uses_tier_after_rescore(self) -> None:
        class Lifecycle:
            def score_channel(self, channel_id):
                from src.crawl_yt.database.models import ChannelScore
                return ChannelScore(channel_id, 80, 80, 80, 80, 80, "high", {}, datetime.now(timezone.utc), "v2")
        report = ChannelCrawlService(FakeVideoProvider(), self.repository, scoring_lifecycle=Lifecycle()).crawl(
            "UC123", now=datetime(2026, 8, 13, tzinfo=timezone.utc)
        )
        self.assertIsNone(report.scoring_error)
        state = self.repository.get_channel_crawl_state("UC123")
        self.assertEqual(state.next_crawl_at, datetime(2026, 8, 16, tzinfo=timezone.utc))

    def test_successful_crawl_uses_medium_and_low_intervals(self) -> None:
        class Lifecycle:
            def __init__(self, tier): self.tier = tier
            def score_channel(self, channel_id):
                from src.crawl_yt.database.models import ChannelScore
                return ChannelScore(channel_id, 50, 50, 50, 50, 50, self.tier, {}, datetime.now(timezone.utc), "v2")
        base = datetime(2026, 8, 13, tzinfo=timezone.utc)
        for tier, days in (("medium", 7), ("low", 14)):
            self.repository.upsert_channel(Channel(f"{tier}-channel", tier))
            service = ChannelCrawlService(
                FakeVideoProvider(), self.repository, scoring_lifecycle=Lifecycle(tier)
            )
            service.crawl(f"{tier}-channel", now=base, full=True)
            state = self.repository.get_channel_crawl_state(f"{tier}-channel")
            self.assertEqual(state.next_crawl_at, base + timedelta(days=days))


if __name__ == "__main__":
    unittest.main()
