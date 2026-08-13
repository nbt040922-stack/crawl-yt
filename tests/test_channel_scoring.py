"""Network-free deterministic channel scoring tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.collectors.channel_collector import ChannelCrawlService
from src.crawl_yt.database.models import Channel, ChannelScore, Video
from src.crawl_yt.database.repository import VideoRepository
from src.crawl_yt.discovery.channel_scoring import (
    SCORING_VERSION,
    ChannelScoringService,
    CrawlPriorityPolicy,
    score_tier,
)


NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


class EmptyProvider:
    def iterate_videos(self, channel_id, limit=None):
        return iter(())


class ChannelScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(Path(self.temp.name) / "test.db")
        self.service = ChannelScoringService(self.repository)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_channel(
        self,
        channel_id: str,
        *,
        subscribers: int | None = None,
        views: int | None = None,
        videos: int | None = None,
        keywords: int = 1,
    ) -> None:
        self.repository.upsert_channel(
            Channel(
                channel_id,
                channel_id,
                subscriber_count=subscribers,
                view_count=views,
                video_count=videos,
            )
        )
        for number in range(keywords):
            self.repository.record_discovery(channel_id, f"keyword-{number}", "test", NOW)

    def add_videos(
        self,
        channel_id: str,
        ages: list[int | None],
        view_counts: list[int | None] | None = None,
    ) -> None:
        view_counts = view_counts or [None] * len(ages)
        for number, (age, views) in enumerate(zip(ages, view_counts, strict=True)):
            self.repository.upsert_video(
                Video(
                    f"{channel_id}-{number}",
                    channel_id,
                    f"Video {number}",
                    NOW,
                    published_at=NOW - timedelta(days=age) if age is not None else None,
                    view_count=views,
                    metadata_enriched_at=NOW if views is not None else None,
                )
            )

    def test_additive_table_and_foreign_key(self) -> None:
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE name='channel_scores'"
            ).fetchone()
        self.assertEqual(table, ("channel_scores",))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.upsert_channel_score(self.manual_score("missing", 50))

    def test_deterministic_clamped_and_versioned(self) -> None:
        self.add_channel("UC1", subscribers=10**12, views=10**15, videos=10**8, keywords=10)
        self.add_videos("UC1", [0] * 25, [10**12] * 25)
        first = self.service.score_channel("UC1", NOW)
        second = self.service.score_channel("UC1", NOW)
        self.assertEqual(first, second)
        self.assertTrue(0 <= first.score <= 100)
        self.assertTrue(all(0 <= value <= 100 for value in (
            first.relevance_score, first.activity_score,
            first.traction_score, first.confidence_score,
        )))
        self.assertEqual(first.scoring_version, SCORING_VERSION)

    def test_multiple_keywords_raise_relevance(self) -> None:
        self.add_channel("UC1", keywords=1)
        self.add_channel("UC2", keywords=3)
        self.assertGreater(
            self.service.score_channel("UC2", NOW).relevance_score,
            self.service.score_channel("UC1", NOW).relevance_score,
        )

    def test_recent_activity_beats_stale(self) -> None:
        self.add_channel("recent")
        self.add_channel("stale")
        self.add_videos("recent", [1, 5, 10, 20])
        self.add_videos("stale", [400, 450, 500, 600])
        self.assertGreater(
            self.service.score_channel("recent", NOW).activity_score,
            self.service.score_channel("stale", NOW).activity_score,
        )

    def test_missing_dates_lower_confidence_not_activity(self) -> None:
        self.add_channel("known")
        self.add_channel("missing")
        self.add_videos("known", [1, 2, 3])
        self.add_videos("missing", [None, None, None])
        known = self.service.score_channel("known", NOW)
        missing = self.service.score_channel("missing", NOW)
        self.assertEqual(missing.activity_score, 50)
        self.assertLess(missing.confidence_score, known.confidence_score)

    def test_log_scaling_caps_huge_subscriber_advantage(self) -> None:
        self.add_channel("large", subscribers=10_000_000)
        self.add_channel("huge", subscribers=100_000_000)
        large = self.service.score_channel("large", NOW)
        huge = self.service.score_channel("huge", NOW)
        self.assertEqual(large.traction_score, huge.traction_score)

    def test_median_ignores_viral_outlier(self) -> None:
        self.add_channel("normal")
        self.add_channel("viral")
        self.add_videos("normal", [1, 2, 3], [100, 110, 120])
        self.add_videos("viral", [1, 2, 3], [100, 110, 1_000_000_000])
        normal = self.service.score_channel("normal", NOW)
        viral = self.service.score_channel("viral", NOW)
        self.assertEqual(normal.reasons["median_enriched_views"], 110)
        self.assertEqual(normal.traction_score, viral.traction_score)

    def test_insufficient_view_sample_and_missing_subscribers(self) -> None:
        self.add_channel("UC1", subscribers=None)
        self.add_videos("UC1", [1, 2], [100, 200])
        result = self.service.score_channel("UC1", NOW)
        self.assertIsNone(result.reasons["median_enriched_views"])
        self.assertIn("insufficient enriched view sample", result.reasons["notes"])

    def test_tier_thresholds(self) -> None:
        self.assertEqual(score_tier(70), "high")
        self.assertEqual(score_tier(69.99), "medium")
        self.assertEqual(score_tier(40), "medium")
        self.assertEqual(score_tier(39.99), "low")

    def test_reason_roundtrip_upsert_and_top_order(self) -> None:
        for channel_id, keywords in (("UC1", 1), ("UC2", 3)):
            self.add_channel(channel_id, keywords=keywords)
            self.service.score_channel(channel_id, NOW)
        self.repository.upsert_channel_score(self.manual_score("UC1", 99))
        stored = self.repository.get_channel_score("UC1")
        self.assertEqual(stored.reasons, {"notes": ["manual"]})
        top = self.repository.list_top_channels(2)
        self.assertEqual(top[0][0].channel_id, "UC1")

    def test_unscored_channels_and_score_all_limit(self) -> None:
        for channel_id in ("UC1", "UC2", "UC3"):
            self.add_channel(channel_id)
        results = self.service.score_all(2, NOW)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.repository.list_unscored_channels(10)), 1)
        counts = self.repository.count_channels_by_score_tier()
        self.assertEqual(counts["unscored"], 1)

    def test_score_based_crawl_interval(self) -> None:
        self.add_channel("high")
        self.add_channel("low")
        self.repository.upsert_channel_score(self.manual_score("high", 80, "high"))
        self.repository.upsert_channel_score(self.manual_score("low", 20, "low"))
        service = ChannelCrawlService(EmptyProvider(), self.repository)
        service.crawl("high")
        service.crawl("low")
        high = self.repository.get_channel_crawl_state("high")
        low = self.repository.get_channel_crawl_state("low")
        high_interval = high.next_crawl_at - high.last_success_at
        low_interval = low.next_crawl_at - low.last_success_at
        self.assertEqual(high_interval, timedelta(hours=12))
        self.assertEqual(low_interval, timedelta(hours=72))
        self.assertLess(high_interval, low_interval)
        self.assertEqual(CrawlPriorityPolicy().interval_for(None), timedelta(hours=24))

    @staticmethod
    def manual_score(channel_id: str, score: float, tier: str = "medium") -> ChannelScore:
        return ChannelScore(
            channel_id, score, score, score, score, score, tier,
            {"notes": ["manual"]}, NOW, SCORING_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
