"""Network-free channel crawl provider and service tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.crawl_yt.collectors.channel_collector import (
    ChannelCrawlService,
    UnknownChannelError,
)
from src.crawl_yt.collectors.ytdlp_channel_video import normalize_video
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
        report = ChannelCrawlService(FakeVideoProvider(), self.repository, scoring_lifecycle=Lifecycle()).crawl("UC123")
        self.assertEqual(report.scoring_error, "score failed")
        self.assertEqual(self.repository.get_channel_crawl_state("UC123").total_crawls, 1)


if __name__ == "__main__":
    unittest.main()
