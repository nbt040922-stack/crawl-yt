from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.normalization import normalize_discovery_keyword
from src.crawl_yt.discovery.channel_discovery import ChannelVerification, DiscoveryBatch, DiscoveryService


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

            def verify(self, channel, sample_size=20):
                titles = [
                    "retirement planning" if index % 2 == 0 else "planning for living alone"
                    for index in range(sample_size)
                ]
                return ChannelVerification(channel, titles)

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            provider = Provider()
            DiscoveryService(provider, repository).discover(
                "  Retirement   Planning ", 1, related_terms=["living alone"]
            )
            self.assertEqual(provider.query, "Retirement Planning")
            self.assertEqual(repository.list_discovery_keyword_summaries()[0]["keyword"], "retirement planning")

    def test_legacy_audit_rows_receive_safe_query_snapshot_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """CREATE TABLE discovery_relevance_runs (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       keyword TEXT NOT NULL,
                       mode TEXT NOT NULL,
                       target_accepted INTEGER NOT NULL,
                       maximum_candidates INTEGER NOT NULL,
                       profile_id INTEGER,
                       profile_name TEXT,
                       effective_concepts_json TEXT NOT NULL,
                       summary_json TEXT NOT NULL,
                       candidate_evidence_json TEXT NOT NULL,
                       created_at TEXT NOT NULL
                       )"""
                )
                connection.execute(
                    """INSERT INTO discovery_relevance_runs
                       (keyword, mode, target_accepted, maximum_candidates,
                        profile_id, profile_name, effective_concepts_json,
                        summary_json, candidate_evidence_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "retirement", "balanced", 1, 100, None, None,
                        '["retirement"]', '{}', '[]',
                        datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            repository = ChannelRepository(database_path)
            ChannelRepository(database_path)
            snapshot = repository.get_discovery_relevance_run(1)

            self.assertEqual(snapshot["planned_queries"], [])
            self.assertEqual(snapshot["executed_queries"], [])
            self.assertEqual(snapshot["query_metrics"], [])
            with repository._connect() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(discovery_relevance_runs)")
                }
            self.assertTrue({
                "planned_queries_json", "executed_queries_json", "query_metrics_json",
            }.issubset(columns))


if __name__ == "__main__":
    unittest.main()
