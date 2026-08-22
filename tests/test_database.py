"""SQLite channel and discovery provenance tests."""

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
            last_checked_at=datetime.now(timezone.utc),
        )

    def test_database_creation(self) -> None:
        self.assertTrue(self.database_path.is_file())
        with closing(sqlite3.connect(self.database_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue({"channels", "channel_discoveries"} <= tables)

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

    def test_one_channel_can_have_multiple_keywords(self) -> None:
        self.repository.upsert_channel(self.channel())
        self.assertTrue(
            self.repository.record_discovery("UC123", "retirement", "test")
        )
        self.assertTrue(
            self.repository.record_discovery("UC123", "social security", "test")
        )
        discoveries = self.repository.list_discoveries_for_channel("UC123")
        self.assertEqual({item.keyword for item in discoveries}, {"retirement", "social security"})

    def test_duplicate_discovery_relationship_is_ignored(self) -> None:
        self.repository.upsert_channel(self.channel())
        self.assertTrue(
            self.repository.record_discovery("UC123", "retirement", "test")
        )
        self.assertFalse(
            self.repository.record_discovery("UC123", "retirement", "test")
        )
        self.assertEqual(self.repository.count_discovery_relationships(), 1)

    def test_discovery_keyword_counts(self) -> None:
        self.repository.upsert_channel(self.channel())
        self.repository.upsert_channel(Channel("UC456", "Second"))
        self.repository.record_discovery("UC123", "retirement", "test")
        self.repository.record_discovery("UC123", "social security", "test")
        self.repository.record_discovery("UC456", "retirement", "test")
        self.assertEqual(
            self.repository.discovery_keyword_counts(),
            [("retirement", 2), ("social security", 1)],
        )
        self.assertEqual(self.repository.count_channels_for_keyword("retirement"), 2)

    def test_foreign_keys_are_enabled_and_enforced(self) -> None:
        with self.repository._connect() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.record_discovery("UC-missing", "retirement", "test")

    def test_topic_profile_migration_preserves_legacy_matching_concepts(self) -> None:
        self.temporary_directory.cleanup()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "legacy.db"
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """CREATE TABLE topic_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    concept_phrases_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """INSERT INTO topic_profiles
                   (name, description, concept_phrases_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    "Legacy Solo Aging", "", '["living alone"]',
                    "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                ),
            )
            connection.commit()

        self.repository = ChannelRepository(self.database_path)
        legacy = self.repository.get_topic_profile(1)
        self.assertEqual(legacy.concept_phrases, ["living alone"])
        self.assertEqual(legacy.search_concepts, [])
        with self.repository._connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(topic_profiles)").fetchall()
            }
        self.assertIn("search_concepts_json", columns)


if __name__ == "__main__":
    unittest.main()
