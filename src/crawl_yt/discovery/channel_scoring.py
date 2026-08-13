"""Deterministic, local-only channel scoring and crawl priority policy."""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

from ..database.models import Channel, ChannelScore
from ..database.repository import VideoRepository

SCORING_VERSION = "v1"
WEIGHTS = {
    "relevance": 0.35,
    "activity": 0.30,
    "traction": 0.20,
    "confidence": 0.15,
}
HIGH_SCORE = 70
MEDIUM_SCORE = 40


def score_tier(score: float) -> str:
    if score >= HIGH_SCORE:
        return "high"
    if score >= MEDIUM_SCORE:
        return "medium"
    return "low"


class CrawlPriorityPolicy:
    INTERVALS = {
        "high": timedelta(hours=12),
        "medium": timedelta(hours=24),
        "low": timedelta(hours=72),
        "unscored": timedelta(hours=24),
    }

    def interval_for(self, tier: str | None) -> timedelta:
        return self.INTERVALS.get(tier or "unscored", self.INTERVALS["unscored"])


class ChannelScoringService:
    def __init__(self, repository: VideoRepository) -> None:
        self.repository = repository

    def score_channel(
        self, channel_id: str, now: datetime | None = None
    ) -> ChannelScore:
        scored_at = now or datetime.now(timezone.utc)
        if scored_at.tzinfo is None:
            scored_at = scored_at.replace(tzinfo=timezone.utc)
        signals = self.repository.get_channel_scoring_signals(channel_id, scored_at)
        if signals is None:
            raise ValueError(f"channel {channel_id} is not in the database")
        channel = signals["channel"]
        assert isinstance(channel, Channel)

        keyword_count = int(signals["discovery_keywords"])
        relevance = min(100.0, 45.0 + 35.0 * math.log2(keyword_count)) if keyword_count else 0.0

        latest = signals["latest_published_at"]
        published = int(signals["published_videos"])
        recent_30 = int(signals["videos_last_30d"])
        recent_90 = int(signals["videos_last_90d"])
        latest_days: int | None = None
        if isinstance(latest, datetime):
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            latest_days = max(0, (scored_at - latest).days)
            recency = max(0.0, 100.0 - latest_days / 1.8)
            activity = (
                recency * 0.60
                + min(100.0, recent_30 * 12.5) * 0.25
                + min(100.0, recent_90 * 5.0) * 0.15
            )
        else:
            activity = 50.0

        views = [int(value) for value in signals["enriched_view_counts"]]
        median_views = float(statistics.median(views)) if len(views) >= 3 else None
        traction_parts: list[float] = []
        if channel.subscriber_count is not None:
            traction_parts.append(min(100.0, math.log10(channel.subscriber_count + 1) / 7 * 100))
        if channel.view_count is not None:
            traction_parts.append(min(100.0, math.log10(channel.view_count + 1) / 10 * 100))
        if channel.video_count is not None:
            traction_parts.append(min(100.0, math.log10(channel.video_count + 1) / 4 * 100))
        if median_views is not None:
            traction_parts.append(min(100.0, math.log10(median_views + 1) / 6 * 100))
            if channel.subscriber_count:
                traction_parts.append(min(100.0, median_views / channel.subscriber_count * 500))
        traction = statistics.mean(traction_parts) if traction_parts else 50.0

        observed = int(signals["observed_videos"])
        enriched = int(signals["enriched_videos"])
        metadata_fields = sum(
            value is not None
            for value in (channel.subscriber_count, channel.view_count, channel.video_count)
        )
        confidence = (
            metadata_fields / 3 * 25
            + min(1.0, observed / 20) * 25
            + (published / observed if observed else 0) * 25
            + min(1.0, enriched / 10) * 25
        )

        components = (relevance, activity, traction, confidence)
        relevance, activity, traction, confidence = [
            round(max(0.0, min(100.0, value)), 2) for value in components
        ]
        final = round(
            max(
                0.0,
                min(
                    100.0,
                    relevance * WEIGHTS["relevance"]
                    + activity * WEIGHTS["activity"]
                    + traction * WEIGHTS["traction"]
                    + confidence * WEIGHTS["confidence"],
                ),
            ),
            2,
        )
        notes: list[str] = []
        if keyword_count >= 2:
            notes.append("strong multi-keyword relevance")
        if latest_days is not None and latest_days <= 30:
            notes.append("active uploader")
        if not published:
            notes.append("publication dates unavailable; activity is neutral")
        if len(views) < 3:
            notes.append("insufficient enriched view sample")
        reasons = {
            "discovery_keywords": keyword_count,
            "latest_video_days_ago": latest_days,
            "videos_last_30d": recent_30,
            "videos_last_90d": recent_90,
            "subscriber_count": channel.subscriber_count,
            "observed_videos": observed,
            "published_videos": published,
            "enriched_videos": enriched,
            "median_enriched_views": median_views,
            "notes": notes,
        }
        result = ChannelScore(
            channel_id=channel_id,
            score=final,
            relevance_score=relevance,
            activity_score=activity,
            traction_score=traction,
            confidence_score=confidence,
            tier=score_tier(final),
            reasons=reasons,
            scored_at=scored_at,
            scoring_version=SCORING_VERSION,
        )
        self.repository.upsert_channel_score(result)
        return result

    def score_all(
        self, limit: int, now: datetime | None = None
    ) -> list[ChannelScore]:
        return [
            self.score_channel(channel.channel_id, now)
            for channel in self.repository.list_channels(limit=limit)
        ]
