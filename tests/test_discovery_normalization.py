from __future__ import annotations

import inspect
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.normalization import normalize_discovery_keyword
from src.crawl_yt.discovery.channel_discovery import DiscoveryBatch, DiscoveryService


class DiscoveryNormalizationTests(unittest.TestCase):
    def test_canonical_normalization_preserves_unicode_and_punctuation(self) -> None:
        self.assertEqual(normalize_discovery_keyword(" Retirement "), "retirement")
        self.assertEqual(normalize_discovery_keyword("social   security"), "social security")
        self.assertEqual(normalize_discovery_keyword("NGHỈ   HƯU"), "nghỉ hưu")
        self.assertEqual(normalize_discovery_keyword("retirement & investing"), "retirement & investing")

    def test_record_discovery_deduplicates_logical_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            repository.upsert_channel(Channel("UC1", "One"))
            self.assertTrue(repository.record_discovery("UC1", "Retirement", "search"))
            self.assertFalse(repository.record_discovery("UC1", " retirement ", "search"))
            self.assertFalse(repository.record_discovery("UC1", "RETIREMENT", "search"))
            self.assertEqual(repository.count_discovery_relationships(), 1)
            with repository._connect() as connection:
                row = connection.execute("SELECT keyword, normalized_keyword FROM channel_discoveries").fetchone()
            self.assertEqual(tuple(row), ("retirement", "retirement"))

    def test_history_aggregation_is_sql_grouped(self) -> None:
        source = inspect.getsource(ChannelRepository.list_discovery_keyword_summaries)
        self.assertIn("GROUP BY normalized_keyword", source)
        self.assertIn("COUNT(DISTINCT channel_id)", source)
        self.assertNotIn("grouped =", source)

    def test_count_channels_for_keyword_uses_canonical_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            repository.upsert_channel(Channel("UC1", "One"))
            repository.record_discovery("UC1", "retirement", "seed")
            self.assertEqual(repository.count_channels_for_keyword(" RETIREMENT "), 1)

    def test_history_uses_distinct_counts_and_min_max(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            repository.upsert_channel(Channel("UC1", "One"))
            first = datetime(2026, 1, 1, tzinfo=timezone.utc)
            last = datetime(2026, 1, 3, tzinfo=timezone.utc)
            repository.record_discovery("UC1", "retirement", "a", first)
            repository.record_discovery("UC1", "retirement", "b", last)
            summary = repository.list_discovery_keyword_summaries()[0]
            self.assertEqual(summary["channel_count"], 1)
            self.assertEqual(summary["first_discovered"], first.isoformat())
            self.assertEqual(summary["last_discovered"], last.isoformat())

    def test_service_separates_trimmed_search_query_from_canonical_provenance(self) -> None:
        class Provider:
            def __init__(self):
                self.query = None

            def search(self, keyword, limit):
                self.query = keyword
                return DiscoveryBatch(1, [Channel("UC1", "One")], "search")

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            provider = Provider()
            DiscoveryService(provider, repository).discover("  Retirement   Planning ", 1)
            self.assertEqual(provider.query, "Retirement Planning")
            self.assertEqual(repository.list_discovery_keyword_summaries()[0]["keyword"], "retirement planning")


if __name__ == "__main__":
    unittest.main()
