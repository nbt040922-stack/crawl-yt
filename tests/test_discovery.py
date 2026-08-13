"""Network-free discovery normalization and service tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.channel_discovery import DiscoveryBatch, DiscoveryService
from src.crawl_yt.discovery.ytdlp_provider import normalize_channel


class FakeProvider:
    def search(self, keyword: str, limit: int) -> DiscoveryBatch:
        return DiscoveryBatch(
            search_results=2,
            channels=[Channel("UC123", "Example"), Channel("UC123", "Example")],
            source="test",
        )


class DiscoveryTests(unittest.TestCase):
    def test_normalizes_stable_channel_id(self) -> None:
        channel = normalize_channel(
            {
                "channel_id": "UC123",
                "channel": "Example Channel",
                "channel_url": "/channel/UC123/",
                "channel_follower_count": 42,
            }
        )
        self.assertIsNotNone(channel)
        self.assertEqual(channel.channel_id, "UC123")
        self.assertEqual(channel.channel_url, "https://www.youtube.com/channel/UC123")
        self.assertEqual(channel.subscriber_count, 42)

    def test_skips_ambiguous_uploader_handle(self) -> None:
        self.assertIsNone(
            normalize_channel({"uploader_id": "@example", "uploader": "Example"})
        )

    def test_existing_channel_with_new_keyword_adds_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            service = DiscoveryService(FakeProvider(), repository)
            first = service.discover("retirement")
            second = service.discover("social security")

            self.assertEqual(first.duplicate_results_in_search, 1)
            self.assertEqual(first.new_channels, 1)
            self.assertEqual(second.existing_channels, 1)
            self.assertEqual(second.new_discovery_relationships, 1)
            self.assertEqual(repository.count_channels(), 1)
            self.assertEqual(repository.count_discovery_relationships(), 2)


if __name__ == "__main__":
    unittest.main()
