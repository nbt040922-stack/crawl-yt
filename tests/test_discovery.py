"""Network-free discovery normalization and service tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.repository import ChannelRepository, VideoRepository
from src.crawl_yt.discovery.channel_discovery import DiscoveryBatch, DiscoveryService
from src.crawl_yt.discovery.channel_scoring import ChannelScoringService
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
            repository = VideoRepository(Path(directory) / "test.db")
            service = DiscoveryService(FakeProvider(), repository)
            first = service.discover("retirement")
            second = service.discover("social security")

            self.assertEqual(first.duplicate_results_in_search, 1)
            self.assertEqual(first.new_channels, 1)
            self.assertEqual(second.existing_channels, 1)
            self.assertEqual(second.new_discovery_relationships, 1)
            self.assertEqual(repository.count_channels(), 1)
            self.assertEqual(repository.count_discovery_relationships(), 2)

    def test_discovery_auto_scores_new_and_new_provenance_only(self) -> None:
        class Lifecycle:
            def __init__(self, repository):
                self.calls = []
                self.repository = repository

            def score_channels(self, channel_ids):
                self.calls.append(set(channel_ids))
                for channel_id in channel_ids:
                    ChannelScoringService(self.repository).score_channel(channel_id)
                return type("Result", (), {"channels_scored": len(channel_ids), "scoring_failures": []})()

        with tempfile.TemporaryDirectory() as directory:
            repository = VideoRepository(Path(directory) / "test.db")
            lifecycle = Lifecycle(repository)
            service = DiscoveryService(FakeProvider(), repository, lifecycle)
            first = service.discover("retirement")
            second = service.discover("retirement")
            third = service.discover("social security")
            self.assertEqual(first.channels_scored, 1)
            self.assertEqual(second.channels_scored, 0)
            self.assertEqual(third.channels_scored, 1)
            self.assertEqual(lifecycle.calls, [{"UC123"}, set(), {"UC123"}])

    def test_discovery_dry_run_does_not_score(self) -> None:
        class Lifecycle:
            def score_channels(self, channel_ids):
                raise AssertionError("dry run scored")

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            report = DiscoveryService(FakeProvider(), repository, Lifecycle()).discover("retirement", dry_run=True)
            self.assertEqual(report.channels_scored, 0)
            self.assertEqual(repository.count_channels(), 0)

    def test_scoring_failure_does_not_fail_discovery(self) -> None:
        class Lifecycle:
            def score_channels(self, channel_ids):
                return type("Result", (), {"channels_scored": 0, "scoring_failures": [("UC123", "score failed")]})()

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            report = DiscoveryService(FakeProvider(), repository, Lifecycle()).discover("retirement")
            self.assertEqual(report.new_channels, 1)
            self.assertEqual(report.scoring_failures, [("UC123", "score failed")])


if __name__ == "__main__":
    unittest.main()
