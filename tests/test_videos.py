"""SQLite video persistence tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import VideoRepository


class VideoRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        self.repository = VideoRepository(self.database_path)
        self.repository.upsert_channel(Channel("UC123", "Example"))
        self.first_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def video(self, title: str = "Original") -> Video:
        return Video(
            video_id="video-1",
            channel_id="UC123",
            title=title,
            first_seen_at=self.first_seen,
            view_count=10,
            webpage_url="https://www.youtube.com/watch?v=video-1",
            metadata_source="test",
        )

    def test_videos_table_creation(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='videos'"
            ).fetchone()
        self.assertEqual(table, ("videos",))

    def test_safe_additive_metadata_migration(self) -> None:
        legacy_path = Path(self.temporary_directory.name) / "phase-1b.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE channels (channel_id TEXT PRIMARY KEY, title TEXT NOT NULL);
                CREATE TABLE videos (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    published_at TEXT,
                    duration_seconds INTEGER,
                    view_count INTEGER,
                    like_count INTEGER,
                    comment_count INTEGER,
                    thumbnail_url TEXT,
                    webpage_url TEXT,
                    availability TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    metadata_source TEXT
                );
                INSERT INTO channels VALUES ('UC123', 'Example');
                INSERT INTO videos (video_id, channel_id, title, first_seen_at)
                    VALUES ('video-1', 'UC123', 'Original', '2026-01-01T00:00:00+00:00');
                """
            )
        migrated = VideoRepository(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(videos)")
            }
        self.assertTrue(
            {"tags_json", "categories_json", "language", "metadata_enriched_at"}
            <= columns
        )
        self.assertEqual(migrated.count_videos(), 1)
        self.assertIsNone(migrated.get_video("video-1").metadata_enriched_at)

    def test_video_insert_and_get(self) -> None:
        self.assertTrue(self.repository.upsert_video(self.video()))
        stored = self.repository.get_video("video-1")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.title, "Original")
        self.assertIsNone(stored.metadata_enriched_at)
        self.assertTrue(self.repository.video_exists("video-1"))

    def test_pending_video_queries(self) -> None:
        self.repository.upsert_video(self.video())
        self.repository.upsert_video(
            Video("video-2", "UC123", "Second", self.first_seen)
        )
        pending = self.repository.list_videos_needing_enrichment(limit=1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.repository.count_videos_needing_enrichment(), 2)
        self.assertEqual(self.repository.count_enriched_videos(), 0)

    def test_duplicate_updates_metadata_and_preserves_first_seen(self) -> None:
        self.repository.upsert_video(self.video())
        updated = self.video("Updated")
        updated.first_seen_at = self.first_seen + timedelta(days=5)
        updated.view_count = 99
        self.assertFalse(self.repository.upsert_video(updated))
        stored = self.repository.get_video("video-1")
        self.assertEqual(self.repository.count_videos(), 1)
        self.assertEqual(stored.title, "Updated")
        self.assertEqual(stored.view_count, 99)
        self.assertEqual(stored.first_seen_at, self.first_seen)

    def test_counts_and_list_for_channel(self) -> None:
        self.repository.upsert_video(self.video())
        self.assertEqual(self.repository.count_videos(), 1)
        self.assertEqual(self.repository.count_videos_for_channel("UC123"), 1)
        self.assertEqual(
            [video.video_id for video in self.repository.list_videos_for_channel("UC123")],
            ["video-1"],
        )

    def test_video_channel_foreign_key_is_enforced(self) -> None:
        invalid = self.video()
        invalid.channel_id = "UC-missing"
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.upsert_video(invalid)


if __name__ == "__main__":
    unittest.main()
