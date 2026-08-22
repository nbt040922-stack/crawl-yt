from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.cadence import (
    CadenceStatus,
    cadence_band,
    evaluate_cadence,
    rates_from_dates,
)
from src.crawl_yt.discovery.channel_discovery import (
    ChannelVerification,
    DiscoveryBatch,
    DiscoveryService,
)


class DiscoveryCadenceTests(unittest.TestCase):
    @staticmethod
    def _qualified_videos(channel: Channel, count: int = 20) -> list[Video]:
        now = datetime.now(timezone.utc)
        return [
            Video(f"v{index}", channel.channel_id, "retirement planning", now, published_at=now - timedelta(days=index % 20))
            for index in range(count)
        ]

    def test_cadence_bands_and_boundary(self) -> None:
        self.assertEqual(cadence_band(2.99), "below target")
        self.assertEqual(cadence_band(3.0), "good")
        self.assertEqual(cadence_band(5.0), "very good")
        self.assertEqual(cadence_band(7.0), "excellent")

        self.assertEqual(evaluate_cadence(3.0).status, CadenceStatus.QUALIFIED)
        self.assertEqual(evaluate_cadence(2.99).status, CadenceStatus.BELOW_TARGET)

    def test_missing_cadence_data_is_not_zero(self) -> None:
        result = evaluate_cadence(None)
        self.assertEqual(result.status, CadenceStatus.INSUFFICIENT_DATA)
        self.assertIsNone(result.videos_per_week)
        self.assertEqual(result.reason, "Insufficient cadence data")

    def test_rates_reuse_fixed_30_and_90_day_windows(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        dates = [now - timedelta(days=offset) for offset in (0, 3, 7, 10, 20, 30, 60)]
        rates = rates_from_dates(dates, now)
        self.assertAlmostEqual(rates.videos_per_week_30d, 6 / (30 / 7))
        self.assertAlmostEqual(rates.videos_per_week_90d, 7 / (90 / 7))

    def test_topic_pass_below_cadence_does_not_persist_new_channel(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Active Life")], "search")

            def verify(self, channel, sample_size=20):
                return ChannelVerification(
                    channel,
                    ["retirement planning", "living alone tips"] * 10,
                    [Video(
                        "v1", channel.channel_id, "retirement planning",
                        datetime.now(timezone.utc),
                        published_at=datetime.now(timezone.utc),
                    )],
                )

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            report = DiscoveryService(Provider(), repository).discover(
                "retirement", 1, related_terms=["living alone"]
            )
            self.assertEqual(report.topic_accepted_count, 1)
            self.assertEqual(report.cadence_qualified_count, 0)
            self.assertEqual(report.cadence_below_target_count, 1)
            self.assertIsNone(repository.get_channel("UC1"))

    def test_qualified_new_channel_is_crawled_before_final_acceptance(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Retirement Planning")], "search")

            def verify(self, channel, sample_size=20):
                videos = DiscoveryCadenceTests._qualified_videos(channel)
                return ChannelVerification(channel, [video.title for video in videos], videos)

        class Crawl:
            def __init__(self):
                self.calls = []

            def crawl(self, channel_id, *, full=False):
                self.calls.append((channel_id, full))

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            crawl = Crawl()
            report = DiscoveryService(Provider(), repository, initial_crawl_service=crawl).discover("retirement", 1)
            self.assertEqual(crawl.calls, [("UC1", True)])
            self.assertEqual(report.final_qualified_count, 1)
            self.assertEqual(report.full_crawled_count, 1)
            self.assertIsNotNone(repository.get_channel("UC1"))

    def test_failed_initial_crawl_is_not_finally_qualified(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Retirement Planning")], "search")

            def verify(self, channel, sample_size=20):
                videos = DiscoveryCadenceTests._qualified_videos(channel)
                return ChannelVerification(channel, [video.title for video in videos], videos)

        class Crawl:
            def crawl(self, channel_id, *, full=False):
                raise RuntimeError("crawl unavailable")

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            report = DiscoveryService(Provider(), repository, initial_crawl_service=Crawl()).discover("retirement", 1)
            self.assertEqual(report.final_qualified_count, 0)
            self.assertEqual(report.final_failed_candidates[0].full_crawl_status, "failed")
            self.assertEqual(report.cadence_failed_count, 1)

    def test_early_stop_counts_final_qualified_not_topic_only(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                channels = [Channel("UC2", "Retirement Planning"), Channel("UC1", "Retirement Planning")]
                return DiscoveryBatch(2, channels, "search")

            def verify(self, channel, sample_size=20):
                if channel.channel_id == "UC1":
                    videos = DiscoveryCadenceTests._qualified_videos(channel)
                else:
                    now = datetime.now(timezone.utc)
                    videos = [Video("v", channel.channel_id, "retirement planning", now, published_at=now)]
                return ChannelVerification(channel, ["retirement planning"] * 20, videos)

        with tempfile.TemporaryDirectory() as directory:
            report = DiscoveryService(Provider(), ChannelRepository(Path(directory) / "db.sqlite")).discover("retirement", 1)
            self.assertEqual(report.final_qualified_count, 1)
            self.assertEqual(report.topic_accepted_count, 2)

    def test_existing_channel_below_cadence_is_preserved_without_new_relationship(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Retirement Planning")], "search")

            def verify(self, channel, sample_size=20):
                now = datetime.now(timezone.utc)
                videos = [Video("v", channel.channel_id, "retirement planning", now, published_at=now)]
                return ChannelVerification(channel, ["retirement planning"] * 20, videos)

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            repository.upsert_channel(Channel("UC1", "Retirement Planning"))
            report = DiscoveryService(Provider(), repository).discover("retirement", 1)
            self.assertEqual(report.cadence_below_target_count, 1)
            self.assertEqual(report.new_discovery_relationships, 0)
            self.assertIsNotNone(repository.get_channel("UC1"))


if __name__ == "__main__":
    unittest.main()
