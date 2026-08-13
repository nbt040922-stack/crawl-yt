"""Network-free incremental crawl state and early-stop tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.collectors.channel_collector import ChannelCrawlService
from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import VideoRepository


NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


class StreamingProvider:
    def __init__(self, entries=None, fail_channels=None) -> None:
        self.entries = entries or {}
        self.fail_channels = fail_channels or set()
        self.yielded = 0

    def iterate_videos(self, channel_id: str, limit: int | None = None):
        if channel_id in self.fail_channels:
            raise RuntimeError("provider failure")
        items = self.entries.get(channel_id, [])
        for video in items[:limit]:
            self.yielded += 1
            yield video


class IncrementalCrawlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(Path(self.temp.name) / "test.db")
        self.repository.upsert_channel(Channel("UC1", "One"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def videos(*ids: str, channel_id: str = "UC1") -> list[Video]:
        return [Video(item, channel_id, item, NOW, published_at=NOW) for item in ids]

    def service(self, *ids: str) -> tuple[ChannelCrawlService, StreamingProvider]:
        provider = StreamingProvider({"UC1": self.videos(*ids)})
        return ChannelCrawlService(provider, self.repository), provider

    def test_state_creation_and_foreign_key(self) -> None:
        state = self.repository.ensure_channel_crawl_state("UC1")
        self.assertEqual((state.channel_id, state.total_crawls), ("UC1", 0))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.ensure_channel_crawl_state("missing")

    def test_first_then_incremental_crawl_and_no_duplicates(self) -> None:
        service, _ = self.service("v3", "v2", "v1")
        first = service.crawl("UC1")
        second = service.crawl("UC1", known_stop_threshold=2)
        self.assertEqual(first.mode, "full")
        self.assertEqual(second.mode, "incremental")
        self.assertTrue(second.stopped_early)
        self.assertEqual(second.enumerated_entries, 2)
        self.assertEqual(self.repository.count_videos(), 3)

    def test_new_video_resets_known_counter(self) -> None:
        service, _ = self.service("old1", "old2", "old3")
        service.crawl("UC1", full=True)
        service, _ = self.service("old1", "new", "old2", "old3")
        report = service.crawl("UC1", known_stop_threshold=2)
        self.assertEqual(report.enumerated_entries, 4)
        self.assertEqual(report.new_videos, 1)
        self.assertEqual(report.consecutive_known_at_stop, 2)

    def test_full_disables_early_stop(self) -> None:
        service, _ = self.service("v1", "v2", "v3")
        service.crawl("UC1")
        report = service.crawl("UC1", full=True, known_stop_threshold=1)
        self.assertEqual((report.mode, report.enumerated_entries), ("full", 3))
        self.assertFalse(report.stopped_early)

    def test_threshold_is_configurable_and_iterator_stops(self) -> None:
        service, _ = self.service("v1", "v2", "v3", "v4")
        service.crawl("UC1")
        service, provider = self.service("v1", "v2", "v3", "v4")
        report = service.crawl("UC1", known_stop_threshold=3)
        self.assertEqual(report.enumerated_entries, 3)
        self.assertEqual(provider.yielded, 3)

    def test_success_state_and_next_crawl(self) -> None:
        service, _ = self.service("v1")
        service.crawl("UC1")
        state = self.repository.get_channel_crawl_state("UC1")
        self.assertEqual(state.last_seen_video_id, "v1")
        self.assertEqual(state.total_crawls, 1)
        self.assertEqual(state.consecutive_failures, 0)
        self.assertGreaterEqual(state.next_crawl_at, state.last_success_at + timedelta(hours=24))

    def test_failure_increments_and_success_resets(self) -> None:
        provider = StreamingProvider(fail_channels={"UC1"})
        service = ChannelCrawlService(provider, self.repository)
        for expected in (1, 2):
            with self.assertRaisesRegex(RuntimeError, "provider failure"):
                service.crawl("UC1")
            state = self.repository.get_channel_crawl_state("UC1")
            self.assertEqual((state.consecutive_failures, state.total_crawls), (expected, expected))
        service, _ = self.service("v1")
        service.crawl("UC1")
        state = self.repository.get_channel_crawl_state("UC1")
        self.assertEqual((state.consecutive_failures, state.total_crawls), (0, 3))
        self.assertIsNotNone(state.last_success_at)

    def test_midstream_failure_does_not_mark_success(self) -> None:
        class MidstreamFailure:
            def iterate_videos(inner_self, channel_id, limit=None):
                yield self.videos("partial")[0]
                raise RuntimeError("stream interrupted")

        with self.assertRaisesRegex(RuntimeError, "stream interrupted"):
            ChannelCrawlService(MidstreamFailure(), self.repository).crawl("UC1")
        state = self.repository.get_channel_crawl_state("UC1")
        self.assertEqual((state.total_crawls, state.consecutive_failures), (1, 1))
        self.assertIsNone(state.last_success_at)
        self.assertTrue(self.repository.video_exists("partial"))

    def test_due_query_and_counts(self) -> None:
        self.repository.upsert_channel(Channel("UC2", "Two"))
        self.repository.mark_crawl_success("UC1", None, None, now=NOW)
        before_due = NOW + timedelta(hours=23)
        at_due = NOW + timedelta(hours=24)
        self.assertEqual(self.repository.list_channels_due_for_crawl(now=before_due), [])
        due = self.repository.list_channels_due_for_crawl(limit=1, now=at_due)
        self.assertEqual([item.channel_id for item in due], ["UC1"])
        counts = self.repository.crawl_state_counts(now=at_due)
        self.assertEqual(counts, {"never_crawled": 1, "due": 1, "healthy": 1, "failing": 0})

    def test_crawl_due_continues_after_failure(self) -> None:
        self.repository.upsert_channel(Channel("UC2", "Two"))
        for channel_id in ("UC1", "UC2"):
            self.repository.mark_crawl_success(
                channel_id, None, None, now=NOW - timedelta(days=2)
            )
        provider = StreamingProvider(
            {"UC1": self.videos("v1"), "UC2": self.videos("v2", channel_id="UC2")},
            fail_channels={"UC1"},
        )
        report = ChannelCrawlService(provider, self.repository).crawl_due(2)
        self.assertEqual((report.channels_attempted, report.channels_succeeded), (2, 1))
        self.assertEqual(report.failures, [("UC1", "provider failure")])


if __name__ == "__main__":
    unittest.main()
