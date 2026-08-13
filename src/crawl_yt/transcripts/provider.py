"""Transcript provider contract, language policy, and service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from ..database.models import Transcript, TranscriptAttempt, Video
from ..database.repository import TranscriptRepository
from .errors import (
    NoSubtitleError,
    ProviderUnavailableError,
    TransientSubtitleError,
    UnavailableVideoError,
)

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
    attempts: int = 0


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
    for source in ("youtube_manual", "youtube_auto", "opencli", "local_whisper"):
        candidates = [item for item in transcripts if item.source == source]
        language = select_language(
            [item.language for item in candidates], preferred
        )
        if language is not None:
            return next(item for item in candidates if item.language == language)
    return None


class TranscriptPipeline:
    def __init__(
        self,
        youtube: TranscriptProvider,
        repository: TranscriptRepository,
        opencli: TranscriptProvider | None = None,
        local_whisper: TranscriptProvider | None = None,
        max_caption_attempts: int = 3,
    ) -> None:
        self.youtube = youtube
        self.opencli = opencli
        self.local_whisper = local_whisper
        self.repository = repository
        self.max_caption_attempts = max_caption_attempts
        self.last_attempts = 0

    def fetch(
        self,
        video_id: str,
        webpage_url: str | None,
        preferred_languages: tuple[str, ...],
        fallback: bool = False,
        allow_audio: bool = False,
    ) -> TranscriptData:
        self.last_attempts = 0
        last_error: Exception = NoSubtitleError("no transcript provider succeeded")
        for _ in range(self.max_caption_attempts):
            try:
                return self._call(
                    "yt-dlp", self.youtube, video_id, webpage_url, preferred_languages
                )
            except TransientSubtitleError as error:
                last_error = error
                continue
            except NoSubtitleError as error:
                last_error = error
                break
            except UnavailableVideoError:
                raise
            except ProviderUnavailableError as error:
                last_error = error
                break

        if not fallback:
            raise last_error
        if self.opencli is not None:
            try:
                return self._call(
                    "opencli", self.opencli, video_id, webpage_url, preferred_languages
                )
            except ProviderUnavailableError:
                pass
            except Exception as error:
                last_error = error
        if allow_audio and self.local_whisper is not None:
            try:
                return self._call(
                    "local_whisper",
                    self.local_whisper,
                    video_id,
                    webpage_url,
                    preferred_languages,
                )
            except Exception as error:
                last_error = error
        raise last_error

    def _call(
        self,
        name: str,
        provider: TranscriptProvider,
        video_id: str,
        webpage_url: str | None,
        preferred_languages: tuple[str, ...],
    ) -> TranscriptData:
        self.last_attempts += 1
        requested = preferred_languages[0] if preferred_languages else None
        try:
            data = provider.fetch(video_id, webpage_url, preferred_languages)
        except Exception as error:
            status = (
                "unavailable"
                if isinstance(error, ProviderUnavailableError)
                else "failed"
            )
            self._record(video_id, name, requested, status, error)
            raise
        self._record(video_id, name, requested, "success")
        return data

    def _record(
        self,
        video_id: str,
        provider: str,
        language: str | None,
        status: str,
        error: Exception | None = None,
    ) -> None:
        self.repository.record_transcript_attempt(
            TranscriptAttempt(
                video_id=video_id,
                provider=provider,
                requested_language=language,
                status=status,
                attempted_at=datetime.now(timezone.utc),
                error_type=type(error).__name__ if error else None,
                error_message=str(error) if error else None,
            )
        )


class TranscriptService:
    def __init__(
        self,
        provider: TranscriptProvider | TranscriptPipeline,
        repository: TranscriptRepository,
    ) -> None:
        self.provider = provider
        self.repository = repository

    def transcript(
        self,
        video_id: str,
        language: str | None = None,
        force: bool = False,
        fallback: bool = False,
        allow_audio: bool = False,
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
            if isinstance(self.provider, TranscriptPipeline):
                data = self.provider.fetch(
                    video_id,
                    video.webpage_url,
                    preferred,
                    fallback=fallback,
                    allow_audio=allow_audio,
                )
                attempts = self.provider.last_attempts
            else:
                data = self.provider.fetch(video_id, video.webpage_url, preferred)
                attempts = 1
        except Exception as error:
            attempts = (
                self.provider.last_attempts
                if isinstance(self.provider, TranscriptPipeline)
                else 1
            )
            return TranscriptResult(video_id, False, error=str(error), attempts=attempts)
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
        return TranscriptResult(video_id, True, stored, attempts=attempts)

    def transcript_channel(
        self,
        channel_id: str,
        limit: int,
        language: str | None = None,
        fallback: bool = False,
        allow_audio: bool = False,
    ) -> TranscriptBatchReport:
        if self.repository.get_channel(channel_id) is None:
            raise ValueError(f"channel {channel_id} is not in the database")
        preferred = language_preferences(language)
        videos = self.repository.list_videos_needing_transcript(
            channel_id=channel_id, limit=limit, languages=preferred
        )
        return self._batch(videos, language, fallback, allow_audio)

    def transcript_pending(
        self,
        limit: int,
        language: str | None = None,
        fallback: bool = False,
        allow_audio: bool = False,
    ) -> TranscriptBatchReport:
        preferred = language_preferences(language)
        videos = self.repository.list_videos_needing_transcript(
            limit=limit, languages=preferred
        )
        return self._batch(videos, language, fallback, allow_audio)

    def _batch(
        self,
        videos: list[Video],
        language: str | None,
        fallback: bool,
        allow_audio: bool,
    ) -> TranscriptBatchReport:
        report = TranscriptBatchReport()
        for video in videos:
            result = self.transcript(
                video.video_id,
                language,
                fallback=fallback,
                allow_audio=allow_audio,
            )
            report.attempted += 1
            report.succeeded += result.success
            report.failed += not result.success
            report.results.append(result)
        return report
