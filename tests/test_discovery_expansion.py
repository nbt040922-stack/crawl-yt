"""Network-free bounded discovery expansion tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel, DiscoveryQuery, DiscoveryRun, Video
from src.crawl_yt.database.repository import VideoRepository
from src.crawl_yt.discovery.channel_discovery import DiscoveryBatch
from src.crawl_yt.discovery.channel_scoring import ChannelScoringService
from src.crawl_yt.discovery.expansion import (
    DiscoveryExpansionService,
    ExpansionPlanner,
    normalize_query,
    useful_phrase,
)


NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, results=None, failures=None) -> None:
        self.results = results or {}
        self.failures = failures or set()
        self.calls: list[tuple[str, int]] = []

    def search(self, keyword: str, limit: int) -> DiscoveryBatch:
        query = normalize_query(keyword)
        self.calls.append((query, limit))
        if query in self.failures:
            raise RuntimeError(f"failed {query}")
        channels = self.results.get(query, [])[:limit]
        return DiscoveryBatch(len(channels), channels, "test")


class ExpansionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(Path(self.temp.name) / "test.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_promising(self, channel_id="UC-good", title="Retirement Planning Experts"):
        self.repository.upsert_channel(Channel(channel_id, title))
        for keyword in ("retirement", "social security"):
            self.repository.record_discovery(channel_id, keyword, "seed", NOW)
        return ChannelScoringService(self.repository).score_channel(channel_id, NOW)

    def expand(self, provider, **overrides):
        options = dict(max_depth=2, channel_budget=10, query_budget=10, results_per_query=10)
        options.update(overrides)
        return DiscoveryExpansionService(provider, self.repository).expand("retirement", **options)

    def test_tables_foreign_key_and_unique_query(self) -> None:
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue({"discovery_runs", "discovery_queries"} <= tables)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.add_discovery_query(
                DiscoveryQuery(None, 999, "query", 0, None, "seed", "pending")
            )
        run = self.repository.create_discovery_run(
            DiscoveryRun(None, "seed", NOW, None, "running", 1, 1, 1)
        )
        query = DiscoveryQuery(None, run.id, "same query", 0, None, "seed", "pending")
        self.repository.add_discovery_query(query)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.add_discovery_query(
                DiscoveryQuery(None, run.id, "same query", 1, "seed", "tag", "pending")
            )

    def test_query_normalization_and_junk_filtering(self) -> None:
        self.assertEqual(normalize_query("  Retirement   Planning "), "retirement planning")
        self.assertEqual(useful_phrase("The Retirement Planning Channel"), "retirement planning")
        for junk in ("official", "12345", "https://example.com retirement", "the and new"):
            self.assertIsNone(useful_phrase(junk))

    def test_title_tag_and_video_title_generation_and_per_channel_cap(self) -> None:
        self.add_promising()
        self.repository.upsert_video(
            Video(
                "v1", "UC-good", "Social Security Retirement Strategies", NOW,
                tags=["Retirement Income Planning", "Pension Strategy Guide"],
                categories=["Personal Finance Tips"],
            )
        )
        planner = ExpansionPlanner(self.repository, max_per_channel=10)
        candidates = planner.candidates("UC-good", 60, 1, "unrelated seed")
        by_source = {item.source: item.query for item in candidates}
        self.assertIn("retirement income planning", [item.query for item in candidates])
        self.assertIn("social security retirement strategies", [item.query for item in candidates])
        self.assertEqual(by_source["channel_title"], "retirement planning experts")
        self.assertLessEqual(len(ExpansionPlanner(self.repository, 3).candidates("UC-good", 60, 1, "seed")), 3)

    def test_low_score_does_not_expand_but_medium_does(self) -> None:
        low = Channel("UC-low", "Official Channel")
        medium = Channel("UC-good", "Retirement Planning Experts")
        self.add_promising()
        self.repository.upsert_channel(low)
        provider = FakeProvider({"retirement": [low, medium]})
        report = self.expand(provider)
        self.assertIn("retirement planning experts", report.generated_queries)
        self.assertFalse(any("official" in query for query in report.generated_queries))

    def test_depth_and_max_depth_are_enforced(self) -> None:
        good = Channel("UC-good", "Retirement Planning Experts")
        self.add_promising()
        provider = FakeProvider({
            "retirement": [good],
            "retirement planning experts": [Channel("UC-child", "Pension Income Strategies")],
        })
        report = self.expand(provider, max_depth=1)
        queries = self.repository.list_discovery_queries(report.run_id)
        self.assertEqual(queries[0].depth, 0)
        self.assertIn(1, [item.depth for item in queries])
        self.assertEqual(report.max_depth_reached, 1)
        self.assertLessEqual(max(item.depth for item in queries), 1)

    def test_query_budget_stops_run(self) -> None:
        good = Channel("UC-good", "Retirement Planning Experts")
        self.add_promising()
        report = self.expand(FakeProvider({"retirement": [good]}), query_budget=1)
        self.assertEqual((report.queries_executed, report.status), (1, "stopped_budget"))

    def test_channel_budget_is_hard_and_existing_does_not_consume_it(self) -> None:
        self.repository.upsert_channel(Channel("UC-existing", "Existing"))
        channels = [
            Channel("UC-existing", "Existing"),
            Channel("UC-new1", "New One"),
            Channel("UC-new2", "New Two"),
        ]
        report = self.expand(
            FakeProvider({"retirement": channels}),
            channel_budget=1,
            max_depth=1,
            query_budget=1,
        )
        self.assertEqual(report.new_channels, 1)
        self.assertEqual(report.existing_channels, 1)
        self.assertTrue(self.repository.get_channel("UC-new1"))
        self.assertIsNone(self.repository.get_channel("UC-new2"))
        self.assertEqual(report.status, "stopped_budget")

    def test_provenance_uses_actual_expansion_query(self) -> None:
        good = Channel("UC-good", "Retirement Planning Experts")
        child = Channel("UC-child", "Child")
        self.add_promising()
        provider = FakeProvider({
            "retirement": [good],
            "retirement planning experts": [child],
        })
        self.expand(provider, max_depth=1)
        self.assertTrue(
            self.repository.discovery_exists(
                "UC-child", "retirement planning experts", "test"
            )
        )
        self.assertFalse(self.repository.discovery_exists("UC-child", "retirement", "test"))

    def test_failed_query_continues_and_completed_status(self) -> None:
        good = Channel("UC-good", "Retirement Planning Experts")
        self.add_promising()
        self.repository.upsert_video(
            Video("v1", "UC-good", "Pension Income Strategies", NOW)
        )
        provider = FakeProvider(
            {"retirement": [good], "pension income strategies": []},
            failures={"retirement planning experts"},
        )
        report = self.expand(provider, max_depth=1)
        self.assertEqual(len(report.failures), 1)
        self.assertGreaterEqual(report.queries_executed, 2)
        self.assertEqual(report.status, "completed")

    def test_all_queries_failed_marks_run_failed(self) -> None:
        report = self.expand(FakeProvider(failures={"retirement"}))
        self.assertEqual(report.status, "failed")
        self.assertEqual(self.repository.get_discovery_run(report.run_id).status, "failed")

    def test_dry_run_has_no_provider_calls_or_writes(self) -> None:
        self.add_promising()
        provider = FakeProvider()
        before = len(self.repository.list_discovery_runs(100))
        report = self.expand(provider, dry_run=True)
        self.assertEqual(provider.calls, [])
        self.assertEqual(len(self.repository.list_discovery_runs(100)), before)
        self.assertIn("retirement", report.generated_queries)


if __name__ == "__main__":
    unittest.main()
