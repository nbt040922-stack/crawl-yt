"""SQLite channel repository tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.repository import ChannelRepository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nested" / "test.db"
        self.repository = ChannelRepository(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def channel(self, title: str = "Example") -> Channel:
        return Channel(
            channel_id="UC123",
            title=title,
            channel_url="https://www.youtube.com/channel/UC123",
            discovery_keyword="retirement",
            discovered_at=datetime.now(timezone.utc),
            discovery_source="test",
        )

    def test_database_creation(self) -> None:
        self.assertTrue(self.database_path.is_file())
        with closing(sqlite3.connect(self.database_path)) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='channels'"
            ).fetchone()
        self.assertEqual(table, ("channels",))

    def test_channel_upsert_and_get(self) -> None:
        self.assertTrue(self.repository.upsert_channel(self.channel()))
        stored = self.repository.get_channel("UC123")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.title, "Example")

    def test_duplicate_channel_id_updates_one_row(self) -> None:
        self.repository.upsert_channel(self.channel())
        self.assertFalse(self.repository.upsert_channel(self.channel("Updated")))
        self.assertEqual(self.repository.count_channels(), 1)
        self.assertEqual(self.repository.get_channel("UC123").title, "Updated")

    def test_count_channels(self) -> None:
        self.assertEqual(self.repository.count_channels(), 0)
        self.repository.upsert_channel(self.channel())
        self.assertEqual(self.repository.count_channels(), 1)


if __name__ == "__main__":
    unittest.main()
