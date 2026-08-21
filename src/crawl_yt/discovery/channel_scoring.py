"""Deterministic, local-only channel scoring and crawl priority policy."""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

from ..database.models import Channel, ChannelScore
from ..database.repository import VideoRepository
from ..crawl_policy import (
    FULL_REFRESH_INTERVAL,
    CrawlPriorityPolicy,
    failure_retry_interval,
)

SCORING_VERSION = "v2"
WEIGHTS = {
    "relevance": 0.25,
    "cadence": 0.35,
    "traction": 0.25,
    "confidence": 0.15,
}
HIGH_SCORE = 70
MEDIUM_SCORE = 40
CADENCE_WINDOW_WEIGHTS = {"30d": 0.60, "90d": 0.40}
CADENCE_ANCHORS = (
    (0.0, 0.0),
    (0.5, 15.0),
    (1.0, 35.0),
    (2.0, 60.0),
    (3.0, 80.0),
    (4.0, 85.0),
    (5.0, 90.0),
    (6.0, 95.0),
    (7.0, 100.0),
    (8.0, 98.0),
    (10.0, 92.0),
    (14.0, 80.0),
)
MATURITY_PARTIAL_VIDEOS = 3
MATURITY_MATURE_VIDEOS = 10


def score_tier(score: float) -> str:
    if score >= HIGH_SCORE:
        return "high"
    if score >= MEDIUM_SCORE:
        return "medium"
    return "low"


def _interpolate(rate: float, anchors: tuple[tuple[float, float], ...]) -> float:
    for (left_rate, left_score), (right_rate, right_score) in zip(anchors, anchors[1:]):
        if rate <= right_rate:
            fraction = (rate - left_rate) / (right_rate - left_rate)
            return left_score + fraction * (right_score - left_score)
    return anchors[-1][1]


def cadence_score(videos_per_week: float) -> float:
    """Map weekly upload rate to a monotonic-to-7, anti-volume score."""
    rate = max(0.0, float(videos_per_week))
    if rate <= CADENCE_ANCHORS[-1][0]:
        return _interpolate(rate, CADENCE_ANCHORS)
    # Beyond 14/week, decline gradually instead of rewarding upload factories.
    return max(20.0, 80.0 - (rate - 14.0) * 2.0)


def cadence_fit(videos_per_week: float | None) -> str:
    if videos_per_week is None:
        return "unknown"
    rate = float(videos_per_week)
    if rate < 1:
        return "very low"
    if rate < 2:
        return "low"
    if rate < 3:
        return "below target"
    if rate < 5:
        return "good"
    if rate < 7:
        return "very good"
    if rate <= 10:
        return "excellent"
    return "very high"


def relevance_score(keyword_count: int) -> float:
    count = max(0, int(keyword_count))
    if count == 0:
        return 0.0
    if count == 1:
        return 50.0
    if count == 2:
        return 70.0
    if count == 3:
        return 82.0
    if count == 4:
        return 90.0
    return min(100.0, 100.0 - 5.0 / (count - 3))


def _consistency_label(active_weeks_ratio: float | None) -> str:
    if active_weeks_ratio is None:
        return "unknown"
    if active_weeks_ratio >= 0.75:
        return "high"
    if active_weeks_ratio >= 0.45:
        return "medium"
    return "low"


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
        relevance = relevance_score(keyword_count)
        published = int(signals["published_videos"])
        recent_30 = int(signals["videos_last_30d"])
        recent_90 = int(signals["videos_last_90d"])
        rate_30 = recent_30 / (30 / 7) if published else None
        rate_90 = recent_90 / (90 / 7) if published else None
        if rate_30 is None or rate_90 is None:
            cadence = 50.0
            cadence_30_score = cadence_90_score = None
        else:
            cadence_30_score = cadence_score(rate_30)
            cadence_90_score = cadence_score(rate_90)
            cadence = (
                cadence_30_score * CADENCE_WINDOW_WEIGHTS["30d"]
                + cadence_90_score * CADENCE_WINDOW_WEIGHTS["90d"]
            )

        dates = list(signals["published_dates"])
        latest = dates[-1] if dates else None
        latest_days: int | None = None
        if latest is not None:
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            latest_days = max(0, (scored_at - latest).days)
        if dates:
            first = dates[0]
            history_span_days = max(0, (latest - first).days)
            observable_weeks = max(1, min(13, math.ceil(history_span_days / 7) + 1))
            active_weeks = len({((item - first).days // 7) for item in dates})
            active_weeks_ratio = min(1.0, active_weeks / observable_weeks)
        else:
            history_span_days = 0
            active_weeks_ratio = None
        cadence_stability = None
        if rate_30 is not None and rate_90 is not None and max(rate_30, rate_90) > 0:
            cadence_stability = min(rate_30, rate_90) / max(rate_30, rate_90)

        views = [int(value) for value in signals["enriched_view_counts"]]
        recent_views = [int(value) for value in signals["recent_enriched_view_counts"]]
        if len(recent_views) >= 3:
            median_views = float(statistics.median(recent_views))
            median_views_source = "recent"
        else:
            median_views = float(statistics.median(views)) if len(views) >= 3 else None
            median_views_source = "all_observed" if median_views is not None else None
        traction_parts: list[float] = []
        if channel.subscriber_count is not None and channel.subscriber_count >= 0:
            traction_parts.append(min(100.0, math.log10(channel.subscriber_count + 1) / 7 * 100))
        if channel.view_count is not None and channel.view_count >= 0:
            traction_parts.append(min(100.0, math.log10(channel.view_count + 1) / 10 * 100))
        if median_views is not None:
            traction_parts.append(min(100.0, math.log10(median_views + 1) / 6 * 100))
        view_subscriber_ratio = None
        if median_views is not None and channel.subscriber_count:
            view_subscriber_ratio = min(1.0, max(0.0, median_views / channel.subscriber_count))
            traction_parts.append(view_subscriber_ratio * 100)
        traction = statistics.mean(traction_parts) if traction_parts else 50.0

        observed = int(signals["observed_videos"])
        enriched = int(signals["enriched_videos"])
        metadata_fields = sum(
            value is not None
            for value in (channel.subscriber_count, channel.view_count, channel.video_count)
        )
        consistency_evidence = (active_weeks_ratio or 0.0) * (cadence_stability or 0.0)
        confidence = (
            metadata_fields / 3 * 25
            + min(1.0, observed / 20) * 20
            + min(1.0, published / 20) * 20
            + min(1.0, enriched / 10) * 20
            + consistency_evidence * 15
        )
        if not published and observed <= 1:
            # A neutral cadence is not evidence of inactivity; retain a small
            # preliminary confidence floor so discovery expansion remains useful.
            confidence = max(20.0, confidence)
        if published < MATURITY_PARTIAL_VIDEOS:
            maturity = "preliminary"
        elif published < MATURITY_MATURE_VIDEOS or (dates and (latest - dates[0]).days < 60):
            maturity = "partial"
        else:
            maturity = "mature"

        relevance, cadence, traction, confidence = [
            round(max(0.0, min(100.0, value)), 2)
            for value in (relevance, cadence, traction, confidence)
        ]
        final = round(
            max(
                0.0,
                min(
                    100.0,
                    relevance * WEIGHTS["relevance"]
                    + cadence * WEIGHTS["cadence"]
                    + traction * WEIGHTS["traction"]
                    + confidence * WEIGHTS["confidence"],
                ),
            ),
            2,
        )
        notes: list[str] = []
        if not published:
            notes.append("cadence unavailable; neutral score used until videos are observed")
        if len(views) < 3:
            notes.append("insufficient enriched view sample")
        if keyword_count >= 2:
            notes.append("multiple discovery relationships are supporting evidence, not semantic proof")
        reasons = {
            "scoring_version": SCORING_VERSION,
            "discovery_keywords": keyword_count,
            "videos_last_30d": recent_30,
            "videos_last_90d": recent_90,
            "videos_per_week_30d": rate_30,
            "videos_per_week_90d": rate_90,
            "cadence_30d_score": cadence_30_score,
            "cadence_90d_score": cadence_90_score,
            "cadence_score": cadence,
            "cadence_fit": cadence_fit(rate_30),
            "active_weeks_ratio": active_weeks_ratio,
            "cadence_stability": cadence_stability,
            "observation_span_days": history_span_days,
            "observation_coverage_30d": history_span_days >= 21,
            "observation_coverage_90d": history_span_days >= 60,
            "consistency": _consistency_label(active_weeks_ratio),
            "subscriber_count": channel.subscriber_count,
            "observed_videos": observed,
            "published_videos": published,
            "enriched_videos": enriched,
            "median_enriched_views": median_views,
            "median_enriched_views_source": median_views_source,
            "view_subscriber_ratio": view_subscriber_ratio,
            "score_maturity": maturity,
            "latest_video_days_ago": latest_days,
            "notes": notes,
        }
        result = ChannelScore(
            channel_id=channel_id,
            score=final,
            relevance_score=relevance,
            activity_score=cadence,
            traction_score=traction,
            confidence_score=confidence,
            tier=score_tier(final),
            reasons=reasons,
            scored_at=scored_at,
            scoring_version=SCORING_VERSION,
            cadence_score=cadence,
            videos_per_week_30d=rate_30,
            videos_per_week_90d=rate_90,
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
