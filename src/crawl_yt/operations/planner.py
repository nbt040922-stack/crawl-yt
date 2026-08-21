"""Deterministic local work planning and sequential execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..database.models import OperationalBudget, WorkItem, WorkPlan
from ..database.repository import VideoRepository

TIER_BASE = {"high": 300.0, "medium": 200.0, "unscored": 150.0, "low": 100.0}


def _days_ago(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    published = datetime.fromisoformat(value)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return max(0.0, (now - published).total_seconds() / 86400)


class OperationalPlanner:
    def __init__(self, repository: VideoRepository) -> None:
        self.repository = repository

    def plan(
        self,
        budget: OperationalBudget,
        now: datetime | None = None,
    ) -> WorkPlan:
        if min(
            budget.max_channel_crawls,
            budget.max_video_enrichments,
            budget.max_transcripts,
            budget.max_discovery_queries,
        ) < 0:
            raise ValueError("operational budgets must not be negative")
        created_at = now or datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        items: list[WorkItem] = []
        items.extend(self._crawl_items(budget.max_channel_crawls, created_at))
        items.extend(
            self._video_items("enrich_video", budget.max_video_enrichments, created_at)
        )
        items.extend(
            self._video_items("transcript_video", budget.max_transcripts, created_at)
        )
        items.sort(key=lambda item: (-item.priority, item.item_type, item.target_id or ""))
        summary = {
            item_type: sum(item.item_type == item_type for item in items)
            for item_type in (
                "crawl_channel",
                "enrich_video",
                "transcript_video",
            )
        }
        plan = WorkPlan(None, created_at, "planned", budget, summary)
        self.repository.create_work_plan(plan, items)
        return plan

    def _crawl_items(self, limit: int, now: datetime) -> list[WorkItem]:
        items: list[WorkItem] = []
        for row in self.repository.list_crawl_work_candidates(limit, now):
            tier = str(row["tier"] or "unscored")
            next_crawl = datetime.fromisoformat(str(row["next_crawl_at"]))
            if next_crawl.tzinfo is None:
                next_crawl = next_crawl.replace(tzinfo=timezone.utc)
            overdue = max(0.0, (now - next_crawl).total_seconds() / 3600)
            failures = int(row["consecutive_failures"])
            priority = TIER_BASE[tier] + min(50.0, overdue / 24) - failures * 20
            items.append(
                WorkItem(
                    None,
                    0,
                    "crawl_channel",
                    str(row["channel_id"]),
                    round(priority, 3),
                    "pending",
                    {
                        "tier": tier,
                        "score": row["score"],
                        "hours_overdue": round(overdue, 2),
                        "consecutive_failures": failures,
                    },
                    now,
                )
            )
        return items

    def _video_items(
        self, item_type: str, limit: int, now: datetime
    ) -> list[WorkItem]:
        items: list[WorkItem] = []
        for row in self.repository.list_video_work_candidates(item_type, limit, now):
            channel_score = float(row["score"] or 0)
            days = _days_ago(row["published_at"], now)
            recency = 0.0 if days is None else max(0.0, 100.0 - days / 3)
            enriched_bonus = (
                20.0
                if item_type == "transcript_video"
                and row["metadata_enriched_at"] is not None
                else 0.0
            )
            priority = channel_score * 2 + recency + enriched_bonus
            reasons = {
                "channel_score": channel_score,
                "published_days_ago": round(days, 2) if days is not None else None,
            }
            if item_type == "enrich_video":
                reasons["metadata_pending"] = True
            else:
                reasons["metadata_enriched"] = row["metadata_enriched_at"] is not None
                reasons["caption_only"] = True
            items.append(
                WorkItem(
                    None,
                    0,
                    item_type,
                    str(row["video_id"]),
                    round(priority, 3),
                    "pending",
                    reasons,
                    now,
                )
            )
        return items


@dataclass(slots=True)
class ExecutionReport:
    plan_id: int
    attempted: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    status: str = "partial"


class WorkPlanExecutor:
    def __init__(
        self,
        repository: VideoRepository,
        crawl_service,
        metadata_service,
        transcript_service,
    ) -> None:
        self.repository = repository
        self.crawl_service = crawl_service
        self.metadata_service = metadata_service
        self.transcript_service = transcript_service

    def execute(
        self, plan_id: int, max_items: int, retry_failed: bool = False
    ) -> ExecutionReport:
        if max_items < 1:
            raise ValueError("max_items must be positive")
        if self.repository.get_work_plan(plan_id) is None:
            raise ValueError(f"work plan {plan_id} not found")
        statuses = ("pending", "failed") if retry_failed else ("pending",)
        items = self.repository.list_work_items(
            plan_id,
            statuses,
            max_items,
            item_types=("crawl_channel", "enrich_video", "transcript_video"),
        )
        report = ExecutionReport(plan_id)
        self.repository.update_work_plan_status(plan_id, "running")
        for item in items:
            report.attempted += 1
            self.repository.mark_work_item_running(item.id)
            try:
                if item.item_type == "crawl_channel":
                    self.crawl_service.crawl(item.target_id)
                    success, error = True, None
                elif item.item_type == "enrich_video":
                    result = self.metadata_service.enrich(item.target_id)
                    success, error = result.success, result.error
                elif item.item_type == "transcript_video":
                    result = self.transcript_service.transcript(item.target_id)
                    success, error = result.success, result.error
            except Exception as exception:
                success, error = False, str(exception)
            if success:
                self.repository.finish_work_item(item.id, "completed")
                report.completed += 1
            else:
                self.repository.finish_work_item(item.id, "failed", error or "work failed")
                report.failed += 1
        report.status = self.repository.refresh_work_plan_status(plan_id)
        return report
