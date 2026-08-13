"""Network-free selective metadata enrichment tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.crawl_yt.collectors.video_metadata import (
    VideoMetadata,
    VideoMetadataService,
)
from src.crawl_yt.collectors.ytdlp_video_metadata import normalize_metadata
from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import VideoRepository


class FakeMetadataProvider:
    def __init__(
        self,
        failures: set[str] | None = None,
        mismatches: set[str] | None = None,
    ) -> None:
        self.failures = failures or set()
        self.mismatches = mismatches or set()

    def fetch(self, video_id: str, webpage_url: str | None = None) -> VideoMetadata:
        if video_id in self.failures:
            raise RuntimeError("metadata unavailable")
        return VideoMetadata(
            video_id=video_id,
            source="test:full",
            channel_id="UC-other" if video_id in self.mismatches else "UC123",
            title=f"Full {video_id}",
            description="Full description",
            duration_seconds=120,
            view_count=999,
            webpage_url=webpage_url,
            tags=["retirement", "planning"],
            categories=["Education"],
            language="en",
        )


class VideoMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(
            Path(self.temporary_directory.name) / "test.db"
        )
        self.repository.upsert_channel(Channel("UC123", "Example"))
        self.first_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for number in range(1, 4):
            self.repository.upsert_video(
                Video(
                    f"video-{number}",
                    "UC123",
                    f"Flat {number}",
                    self.first_seen,
                    webpage_url=f"https://www.youtube.com/watch?v=video-{number}",
                )
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_provider_normalization(self) -> None:
        metadata = normalize_metadata(
            {
                "id": "video-1",
                "channel_id": "UC123",
                "title": "Full",
                "upload_date": "20260812",
                "tags": ["one", "two"],
                "categories": ["Education"],
                "language": "en",
            }
        )
        self.assertEqual(metadata.video_id, "video-1")
        self.assertEqual(metadata.tags, ["one", "two"])
        self.assertEqual(metadata.categories, ["Education"])
        self.assertEqual(metadata.language, "en")

    def test_successful_single_enrichment_round_trips_json(self) -> None:
        service = VideoMetadataService(FakeMetadataProvider(), self.repository)
        result = service.enrich("video-1")
        stored = self.repository.get_video("video-1")
        self.assertTrue(result.success)
        self.assertEqual(stored.first_seen_at, self.first_seen)
        self.assertEqual(stored.description, "Full description")
        self.assertEqual(stored.tags, ["retirement", "planning"])
        self.assertEqual(stored.categories, ["Education"])
        self.assertEqual(stored.language, "en")
        self.assertIsNotNone(stored.metadata_enriched_at)

    def test_failed_enrichment_remains_pending(self) -> None:
        service = VideoMetadataService(
            FakeMetadataProvider(failures={"video-1"}), self.repository
        )
        result = service.enrich("video-1")
        self.assertFalse(result.success)
        self.assertIsNone(
            self.repository.get_video("video-1").metadata_enriched_at
        )

    def test_enrich_channel_respects_limit(self) -> None:
        service = VideoMetadataService(FakeMetadataProvider(), self.repository)
        report = service.enrich_channel("UC123", limit=2)
        self.assertEqual((report.attempted, report.succeeded), (2, 2))
        self.assertEqual(self.repository.count_enriched_videos(), 2)
        self.assertEqual(self.repository.count_videos_needing_enrichment(), 1)

    def test_batch_continues_after_failure(self) -> None:
        service = VideoMetadataService(
            FakeMetadataProvider(failures={"video-2"}), self.repository
        )
        report = service.enrich_pending(limit=3)
        self.assertEqual((report.attempted, report.succeeded, report.failed), (3, 2, 1))
        self.assertIsNone(
            self.repository.get_video("video-2").metadata_enriched_at
        )

    def test_channel_mismatch_does_not_move_video(self) -> None:
        service = VideoMetadataService(
            FakeMetadataProvider(mismatches={"video-1"}), self.repository
        )
        result = service.enrich("video-1")
        stored = self.repository.get_video("video-1")
        self.assertFalse(result.success)
        self.assertTrue(result.channel_mismatch)
        self.assertEqual(stored.channel_id, "UC123")
        self.assertIsNone(stored.metadata_enriched_at)


if __name__ == "__main__":
    unittest.main()
