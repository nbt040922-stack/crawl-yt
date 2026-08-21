"""Network-free operational planner and sequential executor tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.crawl_yt.database.models import (
    Channel,
    ChannelScore,
    OperationalBudget,
    Transcript,
    Video,
    VideoScore,
    WorkItem,
    WorkPlan,
)
from src.crawl_yt.database.repository import VideoRepository
from src.crawl_yt.operations.planner import OperationalPlanner, WorkPlanExecutor


NOW = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)


class FakeService:
    def __init__(self, failures=None) -> None:
        self.failures = failures or set()
        self.calls: list[str] = []

    def crawl(self, target):
        self.calls.append(target)
        if target in self.failures:
            raise RuntimeError("crawl failed")

    def enrich(self, target):
        self.calls.append(target)
        return SimpleNamespace(success=target not in self.failures, error="enrich failed")

    def transcript(self, target):
        self.calls.append(target)
        return SimpleNamespace(success=target not in self.failures, error="transcript failed")


class OperationalPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(Path(self.temp.name) / "test.db")
        self.planner = OperationalPlanner(self.repository)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def channel(self, channel_id: str, score: float, tier: str, failures: int = 0, overdue_days: int = 1):
        self.repository.upsert_channel(Channel(channel_id, channel_id))
        self.repository.upsert_channel_score(
            ChannelScore(channel_id, score, score, score, score, score, tier, {}, NOW, "v1")
        )
        self.repository.mark_crawl_success(channel_id, None, None, now=NOW - timedelta(days=overdue_days + 1))
        for _ in range(failures):
            self.repository.mark_crawl_failure(channel_id, "failed", now=NOW - timedelta(days=2))

    def video(self, video_id: str, channel_id: str, days: int | None, enriched=False):
        published = NOW - timedelta(days=days) if days is not None else None
        self.repository.upsert_video(
            Video(video_id, channel_id, video_id, NOW, published_at=published,
                  metadata_enriched_at=NOW if enriched else None)
        )

    @staticmethod
    def budget(crawls=0, enrichments=0, transcripts=0, discovery=0):
        return OperationalBudget(crawls, enrichments, transcripts, discovery)

    def test_tables_and_foreign_key(self) -> None:
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"work_plans", "work_items"} <= tables)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repository._connect() as connection:
                connection.execute(
                    "INSERT INTO work_items (plan_id,item_type,target_id,priority,status,reason_json,created_at) VALUES (999,'crawl_channel','UC1',1,'pending','{}',?)",
                    (NOW.isoformat(),),
                )

    def test_plan_creation_and_explicit_budget_validation(self) -> None:
        plan = self.planner.plan(self.budget())
        self.assertEqual((plan.status, plan.summary["crawl_channel"]), ("planned", 0))
        self.assertIsNotNone(self.repository.get_work_plan(plan.id))
        with self.assertRaises(ValueError):
            self.planner.plan(self.budget(crawls=-1))

    def test_all_budgets_are_respected_and_no_duplicates(self) -> None:
        self.channel("UC1", 80, "high")
        self.channel("UC2", 60, "medium")
        for number in range(3):
            self.video(f"v{number}", "UC1", number)
        plan = self.planner.plan(self.budget(1, 2, 1, 1), now=NOW)
        self.assertEqual(plan.summary, {
            "crawl_channel": 1, "enrich_video": 2,
            "transcript_video": 1,
        })
        keys = [(item.item_type, item.target_id) for item in self.repository.list_work_items(plan.id)]
        self.assertEqual(len(keys), len(set(keys)))

    def test_pending_item_in_unfinished_plan_is_not_replanned(self) -> None:
        self.channel("UC1", 80, "high")
        first = self.planner.plan(self.budget(crawls=1), now=NOW)
        second = self.planner.plan(self.budget(crawls=1), now=NOW)
        self.assertEqual(first.summary["crawl_channel"], 1)
        self.assertEqual(second.summary["crawl_channel"], 0)

    def test_crawl_tier_overdue_and_failure_priorities(self) -> None:
        self.channel("high", 80, "high")
        self.channel("medium", 60, "medium", overdue_days=20)
        self.channel("healthy", 55, "medium")
        self.channel("failing", 55, "medium", failures=2)
        plan = self.planner.plan(self.budget(crawls=4), now=NOW)
        items = {item.target_id: item for item in self.repository.list_work_items(plan.id)}
        self.assertGreater(items["high"].priority, items["medium"].priority)
        self.assertGreater(items["medium"].reasons["hours_overdue"], items["high"].reasons["hours_overdue"])
        self.assertGreater(items["healthy"].priority, items["failing"].priority)

    def test_recent_and_high_score_video_priorities(self) -> None:
        self.channel("high", 80, "high")
        self.channel("low", 20, "low")
        self.video("high-old", "high", 100)
        self.video("low-recent", "low", 1)
        self.video("high-recent", "high", 1)
        plan = self.planner.plan(self.budget(enrichments=3), now=NOW)
        items = {item.target_id: item for item in self.repository.list_work_items(plan.id)}
        self.assertGreater(items["high-recent"].priority, items["high-old"].priority)
        self.assertGreater(items["high-recent"].priority, items["low-recent"].priority)

    def test_planner_uses_persisted_video_score_priority(self) -> None:
        self.channel("UC1", 50, "medium")
        self.video("first", "UC1", 10)
        self.video("second", "UC1", 10)
        for video_id, priority in (("first", 20.0), ("second", 95.0)):
            self.repository.upsert_video_score(
                VideoScore(video_id, priority, 50, 50, 50, priority, priority, 80, "high", "{}", NOW, "v1")
            )
        plan = self.planner.plan(self.budget(enrichments=2), now=NOW)
        items = self.repository.list_work_items(plan.id)
        self.assertEqual([item.target_id for item in items], ["second", "first"])

    def test_enriched_video_gets_transcript_bonus(self) -> None:
        self.channel("UC1", 50, "medium")
        self.video("plain", "UC1", 1)
        self.video("enriched", "UC1", 1, enriched=True)
        plan = self.planner.plan(self.budget(transcripts=2), now=NOW)
        items = {item.target_id: item for item in self.repository.list_work_items(plan.id)}
        self.assertGreater(items["enriched"].priority, items["plain"].priority)

    def test_running_item_in_unfinished_plan_is_not_replanned(self) -> None:
        self.channel("UC1", 80, "high")
        old = self.planner.plan(self.budget(crawls=1), now=NOW)
        item = self.repository.list_work_items(old.id)[0]
        self.repository.mark_work_item_running(item.id, NOW)
        self.repository.update_work_plan_status(old.id, "running")
        new = self.planner.plan(self.budget(crawls=1), now=NOW)
        self.assertEqual(new.summary["crawl_channel"], 0)

    def test_zero_discovery_budget_creates_no_discovery_work_item(self) -> None:
        plan = self.planner.plan(self.budget(discovery=0), now=NOW)
        self.assertNotIn("discovery_expand", plan.summary)
        self.assertFalse(
            any(item.item_type == "discovery_expand" for item in self.repository.list_work_items(plan.id))
        )

    def test_deterministic_planning_with_fixed_now(self) -> None:
        self.channel("UC1", 80, "high")
        first = self.planner.plan(self.budget(crawls=1), now=NOW)
        self.repository.finish_work_item(self.repository.list_work_items(first.id)[0].id, "completed", now=NOW)
        self.repository.refresh_work_plan_status(first.id)
        second = self.planner.plan(self.budget(crawls=1), now=NOW)
        a = self.repository.list_work_items(first.id)[0]
        b = self.repository.list_work_items(second.id)[0]
        self.assertEqual((a.target_id, a.priority, a.reasons), (b.target_id, b.priority, b.reasons))

    def test_planning_calls_no_services(self) -> None:
        service = FakeService()
        self.planner.plan(self.budget(), now=NOW)
        self.assertEqual(service.calls, [])

    def make_execution_plan(self, targets):
        plan = WorkPlan(None, NOW, "planned", self.budget(), {"crawl_channel": len(targets), "enrich_video": 0, "transcript_video": 0})
        items = [WorkItem(None, 0, "crawl_channel", target, 100-index, "pending", {}, NOW) for index, target in enumerate(targets)]
        self.repository.create_work_plan(plan, items)
        return plan

    def test_execute_respects_limit_and_completed_not_reexecuted(self) -> None:
        plan = self.make_execution_plan(["a", "b", "c"])
        service = FakeService()
        executor = WorkPlanExecutor(self.repository, service, service, service)
        first = executor.execute(plan.id, 2)
        second = executor.execute(plan.id, 3)
        third = executor.execute(plan.id, 3)
        self.assertEqual((first.attempted, second.attempted, third.attempted), (2, 1, 0))
        self.assertEqual(service.calls, ["a", "b", "c"])
        self.assertEqual(second.status, "completed")

    def test_failure_continues_partial_and_retry_failed(self) -> None:
        plan = self.make_execution_plan(["bad", "good"])
        service = FakeService({"bad"})
        executor = WorkPlanExecutor(self.repository, service, service, service)
        first = executor.execute(plan.id, 2)
        self.assertEqual((first.completed, first.failed, first.status), (1, 1, "partial"))
        service.failures.clear()
        skipped = executor.execute(plan.id, 2)
        retried = executor.execute(plan.id, 2, retry_failed=True)
        self.assertEqual(skipped.attempted, 0)
        self.assertEqual((retried.completed, retried.status), (1, "completed"))


if __name__ == "__main__":
    unittest.main()
