"""Storage-neutral data structures for core entities."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Channel:
    channel_id: str
    title: str
    description: str | None = None
    channel_url: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    last_checked_at: datetime | None = None


@dataclass(slots=True)
class ChannelDiscovery:
    channel_id: str
    keyword: str
    source: str
    discovered_at: datetime


@dataclass(slots=True)
class ChannelCrawlState:
    channel_id: str
    last_crawl_started_at: datetime | None = None
    last_crawl_completed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    last_seen_video_id: str | None = None
    last_seen_published_at: datetime | None = None
    consecutive_failures: int = 0
    total_crawls: int = 0
    next_crawl_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ChannelScore:
    channel_id: str
    score: float
    relevance_score: float
    activity_score: float
    traction_score: float
    confidence_score: float
    tier: str
    reasons: dict[str, Any]
    scored_at: datetime
    scoring_version: str


@dataclass(slots=True)
class DiscoveryRun:
    id: int | None
    seed_keyword: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    max_depth: int
    channel_budget: int
    query_budget: int
    channels_discovered: int = 0
    queries_executed: int = 0
    error_message: str | None = None


@dataclass(slots=True)
class DiscoveryQuery:
    id: int | None
    run_id: int
    query: str
    depth: int
    parent_query: str | None
    source: str
    status: str
    channels_found: int = 0
    new_channels: int = 0
    executed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OperationalBudget:
    max_channel_crawls: int
    max_video_enrichments: int
    max_transcripts: int
    max_discovery_queries: int = 0


@dataclass(slots=True)
class WorkPlan:
    id: int | None
    created_at: datetime
    status: str
    budget: OperationalBudget
    summary: dict[str, int]
    completed_at: datetime | None = None


@dataclass(slots=True)
class WorkItem:
    id: int | None
    plan_id: int
    item_type: str
    target_id: str | None
    priority: float
    status: str
    reasons: dict[str, Any]
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


@dataclass(slots=True)
class Video:
    video_id: str
    channel_id: str
    title: str
    first_seen_at: datetime
    description: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    thumbnail_url: str | None = None
    webpage_url: str | None = None
    availability: str | None = None
    last_checked_at: datetime | None = None
    metadata_source: str | None = None
    tags: list[str] | None = None
    categories: list[str] | None = None
    language: str | None = None
    metadata_enriched_at: datetime | None = None


@dataclass(slots=True)
class VideoScore:
    video_id: str
    score: float
    recency_score: float
    channel_score: float
    traction_score: float
    metadata_value_score: float
    transcript_value_score: float
    confidence_score: float
    tier: str
    reason_json: str
    scored_at: datetime
    scoring_version: str

    @property
    def metadata_priority(self) -> float:
        return float(json.loads(self.reason_json).get("metadata_priority", self.score))

    @property
    def transcript_priority(self) -> float:
        return float(json.loads(self.reason_json).get("transcript_priority", self.transcript_value_score))


@dataclass(slots=True)
class Transcript:
    video_id: str
    language: str
    source: str
    text: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class TranscriptAttempt:
    video_id: str
    provider: str
    requested_language: str | None
    status: str
    attempted_at: datetime
    error_type: str | None = None
    error_message: str | None = None
