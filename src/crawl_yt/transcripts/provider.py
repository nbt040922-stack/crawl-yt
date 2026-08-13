"""Transcript provider contract, language policy, and service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from ..database.models import Transcript, Video
from ..database.repository import TranscriptRepository

DEFAULT_LANGUAGES = ("en", "en-US", "en-GB")


@dataclass(slots=True)
class TranscriptData:
    video_id: str
    language: str
    source: str
    text: str
    segments: list[dict[str, float | str]]


class TranscriptProvider(Protocol):
    def fetch(
        self,
        video_id: str,
        webpage_url: str | None,
        preferred_languages: tuple[str, ...],
    ) -> TranscriptData: ...


@dataclass(slots=True)
class TranscriptResult:
    video_id: str
    success: bool
    transcript: Transcript | None = None
    cached: bool = False
    error: str | None = None


@dataclass(slots=True)
class TranscriptBatchReport:
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[TranscriptResult] = field(default_factory=list)


def language_preferences(language: str | None) -> tuple[str, ...]:
    return (language,) if language else DEFAULT_LANGUAGES


def select_language(
    available: list[str], preferred: tuple[str, ...]
) -> str | None:
    by_lower = {language.lower(): language for language in available}
    for preference in preferred:
        if preference.lower() in by_lower:
            return by_lower[preference.lower()]
    for preference in preferred:
        base = preference.lower().split("-", 1)[0]
        for language in available:
            candidate = language.lower()
            if candidate == base or candidate.startswith(f"{base}-"):
                return language
    return None


def select_existing_transcript(
    transcripts: list[Transcript], preferred: tuple[str, ...]
) -> Transcript | None:
    for source in ("youtube_manual", "youtube_auto"):
        candidates = [item for item in transcripts if item.source == source]
        language = select_language(
            [item.language for item in candidates], preferred
        )
        if language is not None:
            return next(item for item in candidates if item.language == language)
    return None


class TranscriptService:
    def __init__(
        self, provider: TranscriptProvider, repository: TranscriptRepository
    ) -> None:
        self.provider = provider
        self.repository = repository

    def transcript(
        self, video_id: str, language: str | None = None, force: bool = False
    ) -> TranscriptResult:
        video = self.repository.get_video(video_id)
        if video is None:
            return TranscriptResult(video_id, False, error="video is not in the database")
        preferred = language_preferences(language)
        existing = select_existing_transcript(
            self.repository.list_transcripts_for_video(video_id), preferred
        )
        if existing is not None and not force:
            return TranscriptResult(video_id, True, existing, cached=True)
        try:
            data = self.provider.fetch(video_id, video.webpage_url, preferred)
        except Exception as error:
            return TranscriptResult(video_id, False, error=str(error))
        now = datetime.now(timezone.utc)
        transcript = Transcript(
            video_id=data.video_id,
            language=data.language,
            source=data.source,
            text=data.text,
            segments=data.segments,
            created_at=now,
            updated_at=now,
        )
        self.repository.upsert_transcript(transcript)
        stored = self.repository.get_transcript(
            data.video_id, data.language, data.source
        )
        return TranscriptResult(video_id, True, stored)

    def transcript_channel(
        self, channel_id: str, limit: int, language: str | None = None
    ) -> TranscriptBatchReport:
        if self.repository.get_channel(channel_id) is None:
            raise ValueError(f"channel {channel_id} is not in the database")
        preferred = language_preferences(language)
        videos = self.repository.list_videos_needing_transcript(
            channel_id=channel_id, limit=limit, languages=preferred
        )
        return self._batch(videos, language)

    def transcript_pending(
        self, limit: int, language: str | None = None
    ) -> TranscriptBatchReport:
        preferred = language_preferences(language)
        videos = self.repository.list_videos_needing_transcript(
            limit=limit, languages=preferred
        )
        return self._batch(videos, language)

    def _batch(
        self, videos: list[Video], language: str | None
    ) -> TranscriptBatchReport:
        report = TranscriptBatchReport()
        for video in videos:
            result = self.transcript(video.video_id, language)
            report.attempted += 1
            report.succeeded += result.success
            report.failed += not result.success
            report.results.append(result)
        return report
