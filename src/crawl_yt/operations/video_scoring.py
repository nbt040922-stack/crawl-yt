"""Deterministic, network-free video prioritization."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

from ..database.models import Video, VideoScore
from ..database.repository import VideoRepository

SCORING_VERSION = "v1"
STALE_AFTER = timedelta(hours=24)
TIER_THRESHOLDS = ((70.0, "high"), (40.0, "medium"), (0.0, "low"))
RECENCY_BUCKETS = ((1, 100.0), (3, 90.0), (7, 80.0), (14, 65.0), (30, 45.0), (90, 25.0), (float("inf"), 10.0))


def _tier(score: float) -> str:
    for threshold, tier in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "low"


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 3)


class VideoScoringService:
    def __init__(self, repository: VideoRepository, stale_after: timedelta = STALE_AFTER) -> None:
        self.repository = repository
        self.stale_after = stale_after

    def score_video(
        self, video_id: str, now: datetime | None = None, force: bool = False
    ) -> VideoScore:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cached = self.repository.get_video_score(video_id)
        if (
            cached
            and cached.scoring_version == SCORING_VERSION
            and not force
            and current - cached.scored_at < self.stale_after
        ):
            return cached
        inputs = self.repository.get_video_scoring_input(video_id)
        if inputs is None:
            raise ValueError(f"video {video_id} not found")
        video = inputs["video"]
        assert isinstance(video, Video)
        recency = self._recency(video, current)
        channel = _clamp(float(inputs["channel_score"] if inputs["channel_score"] is not None else 50.0))
        traction = self._traction(video, inputs["channel_subscribers"])
        metadata_present = video.metadata_enriched_at is not None
        transcript_present = bool(inputs["transcript_present"])
        metadata_value = _clamp(recency * 0.50 + channel * 0.35 + (100.0 if not metadata_present else 25.0) * 0.15)
        transcript_value = _clamp(
            recency * 0.30
            + channel * 0.30
            + traction * 0.20
            + (100.0 if metadata_present else 25.0) * 0.10
            + self._confidence(video, inputs) * 0.10
        )
        confidence = self._confidence(video, inputs)
        metadata_priority = _clamp(recency * 0.40 + channel * 0.30 + traction * 0.15 + confidence * 0.15)
        transcript_priority = _clamp(recency * 0.30 + channel * 0.30 + traction * 0.20 + metadata_value * 0.10 + confidence * 0.10)
        score = _clamp(max(metadata_priority, transcript_priority))
        reason = {
            "published_days_ago": self._age_days(video, current),
            "view_count": video.view_count,
            "channel_score": channel,
            "metadata_present": metadata_present,
            "transcript_present": transcript_present,
            "metadata_priority": metadata_priority,
            "transcript_priority": transcript_priority,
            "notes": self._notes(video, channel, metadata_present, transcript_present),
        }
        result = VideoScore(
            video.video_id, score, recency, channel, traction, metadata_value,
            transcript_value, confidence, _tier(score),
            json.dumps(reason, ensure_ascii=False, sort_keys=True), current, SCORING_VERSION,
        )
        return self.repository.upsert_video_score(result)

    def score_videos(self, limit: int, now: datetime | None = None) -> list[VideoScore]:
        if limit < 1:
            raise ValueError("limit must be positive")
        current = now or datetime.now(timezone.utc)
        return [self.score_video(video_id, current) for video_id in self.repository.list_video_ids(limit)]

    @staticmethod
    def _age_days(video: Video, now: datetime) -> float | None:
        if video.published_at is None:
            return None
        published = video.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return round(max(0.0, (now - published).total_seconds() / 86400), 3)

    @classmethod
    def _recency(cls, video: Video, now: datetime) -> float:
        age = cls._age_days(video, now)
        if age is None:
            return 50.0
        for max_days, value in RECENCY_BUCKETS:
            if age <= max_days:
                return value
        return 10.0

    @staticmethod
    def _traction(video: Video, subscribers: object) -> float:
        if video.view_count is None or video.view_count < 0:
            return 50.0
        view_score = min(100.0, math.log10(video.view_count + 1) / math.log10(100_000_001) * 100)
        if subscribers is None or int(subscribers) <= 0:
            return round(view_score, 3)
        ratio_score = min(100.0, video.view_count / int(subscribers) * 10)
        return round(view_score * 0.70 + ratio_score * 0.30, 3)

    @staticmethod
    def _confidence(video: Video, inputs: dict[str, object]) -> float:
        available = sum(
            value
            for value in (
                video.published_at is not None,
                video.view_count is not None,
                inputs["channel_score"] is not None,
                inputs["channel_subscribers"] is not None,
                video.metadata_enriched_at is not None,
            )
        )
        return float(20 + available * 16)

    @staticmethod
    def _notes(video: Video, channel: float, metadata_present: bool, transcript_present: bool) -> list[str]:
        notes = []
        if video.published_at is not None:
            notes.append("published date available")
        if channel >= 70:
            notes.append("high-score channel")
        if not metadata_present:
            notes.append("metadata pending")
        if not transcript_present:
            notes.append("transcript pending")
        return notes
