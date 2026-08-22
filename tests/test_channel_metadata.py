from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.crawl_yt.collectors.channel_metadata import ChannelMetadata
from src.crawl_yt.collectors.ytdlp_channel_metadata import normalize_channel_metadata
from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.models import Video
from src.crawl_yt.database.repository import VideoRepository
from src.crawl_yt.discovery.channel_scoring import ChannelScoringService


class ChannelMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(Path(self.temp.name) / "metadata.db")
        self.repository.upsert_channel(Channel("UC1", "Original", subscriber_count=50000))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_provider_mapping_preserves_none_zero_and_channel_fields(self) -> None:
        result = normalize_channel_metadata({"id": "UC1", "channel": "Hydrated", "description": "About", "channel_url": "https://youtube.com/channel/UC1/", "subscriber_count": 0, "view_count": 1234, "video_count": 42})
        self.assertEqual((result.channel_id, result.title, result.subscriber_count, result.view_count, result.video_count), ("UC1", "Hydrated", 0, 1234, 42))
        missing = normalize_channel_metadata({"id": "UC1", "channel": "Hydrated"})
        self.assertIsNone(missing.subscriber_count)

    def test_update_preserves_known_values_when_new_metadata_missing(self) -> None:
        checked = datetime(2026, 8, 22, tzinfo=timezone.utc)
        self.repository.update_channel_metadata(ChannelMetadata("UC1", title="", description="", subscriber_count=None, view_count=None, video_count=None, checked_at=checked))
        channel = self.repository.get_channel("UC1")
        self.assertEqual(channel.subscriber_count, 50000)
        self.assertEqual(channel.title, "Original")
        self.assertEqual(channel.metadata_checked_at, checked)

    def test_update_stores_zero_and_metadata_values(self) -> None:
        self.repository.update_channel_metadata(ChannelMetadata("UC1", title="New", subscriber_count=0, view_count=99, video_count=3))
        channel = self.repository.get_channel("UC1")
        self.assertEqual((channel.title, channel.subscriber_count, channel.view_count, channel.video_count), ("New", 0, 99, 3))

    def test_hydrated_metadata_changes_traction_evidence(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        for index in range(3):
            self.repository.upsert_video(Video(f"v{index}", "UC1", f"Video {index}", now, published_at=now, view_count=10000, metadata_enriched_at=now))
        before = ChannelScoringService(self.repository).score_channel("UC1", now)
        self.repository.update_channel_metadata(ChannelMetadata("UC1", subscriber_count=100000, view_count=1000000))
        after = ChannelScoringService(self.repository).score_channel("UC1", now)
        self.assertNotEqual(before.traction_score, after.traction_score)


if __name__ == "__main__":
    unittest.main()
