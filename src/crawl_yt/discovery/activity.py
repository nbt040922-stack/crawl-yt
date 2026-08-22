"""Cheap, transient activity evidence used only to order discovery work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..database.models import Channel


@dataclass(frozen=True, slots=True)
class DiscoverySearchResult:
    channel: Channel
    video_id: str | None = None
    published_at: datetime | None = None


@dataclass(slots=True)
class CandidateActivitySignal:
    observed_video_ids: set[str] = field(default_factory=set)
    observed_video_dates: dict[str, datetime] = field(default_factory=dict)
    observed_result_count: int = 0
    queries_found_by: set[str] = field(default_factory=set)

    def merge(self, result: DiscoverySearchResult, query: str) -> None:
        self.observed_result_count += 1
        self.queries_found_by.add(query)
        if result.video_id:
            self.observed_video_ids.add(result.video_id)
            if result.published_at is not None:
                self.observed_video_dates[result.video_id] = _utc(result.published_at)

    @property
    def query_diversity(self) -> int:
        return len(self.queries_found_by)

    @property
    def newest_observed_video_at(self) -> datetime | None:
        return max(self.observed_video_dates.values(), default=None)

    @property
    def distinct_observed_videos(self) -> int:
        return len(self.observed_video_ids)

    @property
    def hint(self) -> str:
        return activity_hint(self)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def activity_hint(signal: CandidateActivitySignal, now: datetime | None = None) -> str:
    newest = signal.newest_observed_video_at
    if newest is None:
        return "UNKNOWN"
    age = max(0, (_utc(now or datetime.now(timezone.utc)) - newest).days)
    if age <= 7:
        return "VERY RECENT"
    if age <= 30:
        return "RECENT"
    return "STALE"


def activity_priority_score(signal: CandidateActivitySignal, now: datetime | None = None) -> float:
    """Return explainable 0–100 ordering score; never a qualification gate."""
    hint = activity_hint(signal, now)
    recency = {"VERY RECENT": 60.0, "RECENT": 40.0, "STALE": 10.0, "UNKNOWN": 0.0}[hint]
    distinct = min(25.0, signal.distinct_observed_videos * 8.0)
    diversity = min(10.0, max(0, signal.query_diversity - 1) * 5.0)
    results = min(5.0, signal.observed_result_count)
    return recency + distinct + diversity + results
