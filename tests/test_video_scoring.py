"""Network-free deterministic video scoring tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel, ChannelScore, Video
from src.crawl_yt.database.repository import VideoRepository
from src.crawl_yt.operations.video_scoring import (
    RECENCY_BUCKETS,
    VideoScoringService,
)


NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


class VideoScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(Path(self.temp.name) / "test.db")
        self.service = VideoScoringService(self.repository)
        self.repository.upsert_channel(Channel("UC1", "Channel", subscriber_count=100_000))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_video(self, video_id="v1", days=2, views=183_000, enriched=False):
        self.repository.upsert_video(
            Video(
                video_id,
                "UC1",
                "Video",
                NOW,
                published_at=None if days is None else NOW - timedelta(days=days),
                view_count=views,
                metadata_enriched_at=NOW if enriched else None,
            )
        )

    def add_channel_score(self, score=82.4):
        self.repository.upsert_channel_score(
            ChannelScore("UC1", score, score, score, score, score, "high", {}, NOW, "v1")
        )

    def test_table_fk_and_round_trip(self) -> None:
        self.add_video()
        score = self.service.score_video("v1", NOW)
        with self.repository._connect() as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("video_scores", tables)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO video_scores (video_id,score,recency_score,channel_score,traction_score,metadata_value_score,transcript_value_score,confidence_score,tier,reason_json,scored_at,scoring_version) VALUES ('missing',1,1,1,1,1,1,1,'low','{}',?, 'v1')",
                    (NOW.isoformat(),),
                )
        self.assertEqual(self.repository.get_video_score("v1"), score)

    def test_recency_buckets_and_missing_date(self) -> None:
        for days, expected in RECENCY_BUCKETS[:-1]:
            self.add_video(f"v{days}", days)
            self.assertEqual(self.service.score_video(f"v{days}", NOW).recency_score, expected)
        self.add_video("missing", None)
        self.assertEqual(self.service.score_video("missing", NOW).recency_score, 50.0)

    def test_formula_signals_reason_and_version(self) -> None:
        self.add_channel_score()
        self.add_video(views=183_000, enriched=False)
        score = self.service.score_video("v1", NOW)
        self.assertEqual(score.scoring_version, "v1")
        self.assertGreater(score.channel_score, 80)
        self.assertGreater(score.traction_score, 0)
        self.assertEqual(score.tier, "high")
        reason = json.loads(score.reason_json)
        self.assertEqual(reason["metadata_present"], False)
        self.assertEqual(reason["transcript_present"], False)
        self.assertEqual(reason["view_count"], 183_000)
        self.assertIn("metadata_priority", reason)

    def test_missing_data_is_neutral_and_scores_are_clamped(self) -> None:
        self.add_video("missing", None, None)
        score = self.service.score_video("missing", NOW)
        self.assertEqual((score.recency_score, score.channel_score, score.traction_score), (50.0, 50.0, 50.0))
        self.assertTrue(0 <= score.score <= 100)
        self.assertEqual(score.tier, "medium")

    def test_stale_score_refreshes_but_fresh_score_is_reused(self) -> None:
        self.add_video()
        first = self.service.score_video("v1", NOW - timedelta(hours=1))
        fresh = self.service.score_video("v1", NOW)
        self.assertEqual(first.scored_at, fresh.scored_at)
        stale = self.service.score_video("v1", NOW + timedelta(days=2))
        self.assertGreater(stale.scored_at, fresh.scored_at)


if __name__ == "__main__":
    unittest.main()
