"""Network-free transcript persistence, selection, normalization, and service tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel, Transcript, Video
from src.crawl_yt.database.repository import TranscriptRepository
from src.crawl_yt.transcripts.provider import (
    TranscriptData,
    TranscriptService,
    select_language,
)
from src.crawl_yt.transcripts.ytdlp_provider import (
    normalize_json3,
    normalize_vtt,
    select_subtitle_track,
)


class FakeTranscriptProvider:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[str] = []

    def fetch(
        self,
        video_id: str,
        webpage_url: str | None,
        preferred_languages: tuple[str, ...],
    ) -> TranscriptData:
        self.calls.append(video_id)
        if video_id in self.failures:
            raise RuntimeError("no subtitles")
        language = preferred_languages[0]
        segments = [{"start": 0.0, "end": 1.0, "text": f"Text {video_id}"}]
        return TranscriptData(
            video_id, language, "youtube_manual", f"Text {video_id}", segments
        )


class TranscriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        self.repository = TranscriptRepository(self.database_path)
        self.repository.upsert_channel(Channel("UC123", "Example"))
        self.first_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for number in range(1, 4):
            self.repository.upsert_video(
                Video(
                    f"video-{number}",
                    "UC123",
                    f"Video {number}",
                    self.first_seen,
                    webpage_url=f"https://www.youtube.com/watch?v=video-{number}",
                )
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def transcript(
        self,
        language: str = "en",
        source: str = "youtube_manual",
        text: str = "Hello world",
    ) -> Transcript:
        now = datetime.now(timezone.utc)
        return Transcript(
            video_id="video-1",
            language=language,
            source=source,
            text=text,
            segments=[{"start": 0.0, "end": 1.5, "text": text}],
            created_at=now,
            updated_at=now,
        )

    def test_table_insert_json_round_trip_and_counts(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='transcripts'"
            ).fetchone()
        self.assertEqual(table, ("transcripts",))
        self.assertTrue(self.repository.upsert_transcript(self.transcript()))
        stored = self.repository.get_transcript("video-1", "en", "youtube_manual")
        self.assertEqual(stored.segments[0]["start"], 0.0)
        self.assertEqual(self.repository.count_transcripts(), 1)
        self.assertEqual(self.repository.count_videos_with_transcripts(), 1)
        self.assertEqual(self.repository.count_videos_without_transcripts(), 2)

    def test_upsert_preserves_unique_key_and_created_at(self) -> None:
        original = self.transcript()
        self.repository.upsert_transcript(original)
        refreshed = self.transcript(text="Updated")
        refreshed.created_at = original.created_at + timedelta(days=1)
        self.assertFalse(self.repository.upsert_transcript(refreshed))
        stored = self.repository.get_transcript("video-1", "en", "youtube_manual")
        self.assertEqual(self.repository.count_transcripts(), 1)
        self.assertEqual(stored.text, "Updated")
        self.assertEqual(stored.created_at, original.created_at)

    def test_video_language_source_are_independent_rows(self) -> None:
        self.repository.upsert_transcript(self.transcript("en", "youtube_manual"))
        self.repository.upsert_transcript(self.transcript("en", "youtube_auto"))
        self.repository.upsert_transcript(self.transcript("vi", "youtube_manual"))
        self.assertEqual(self.repository.count_transcripts(), 3)
        self.assertEqual(len(self.repository.list_transcripts_for_video("video-1")), 3)

    def test_foreign_key_is_enforced(self) -> None:
        missing = self.transcript()
        missing.video_id = "missing"
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.upsert_transcript(missing)

    def test_pending_query_uses_requested_language(self) -> None:
        self.repository.upsert_transcript(self.transcript("vi"))
        english_pending = self.repository.list_videos_needing_transcript(
            channel_id="UC123", limit=2, languages=("en",)
        )
        vietnamese_pending = self.repository.list_videos_needing_transcript(
            channel_id="UC123", limit=3, languages=("vi",)
        )
        self.assertEqual(len(english_pending), 2)
        self.assertNotIn("video-1", [video.video_id for video in vietnamese_pending])

    def test_language_selection_exact_then_variant(self) -> None:
        self.assertEqual(select_language(["en-US", "vi"], ("en",)), "en-US")
        self.assertEqual(select_language(["en-US", "en"], ("en",)), "en")
        self.assertIsNone(select_language(["fr"], ("en",)))

    def test_manual_track_preferred_and_auto_fallback(self) -> None:
        manual = {"en": [{"ext": "vtt", "url": "manual"}]}
        automatic = {"en": [{"ext": "vtt", "url": "auto"}]}
        selected = select_subtitle_track(manual, automatic, ("en",))
        self.assertEqual(selected[:2], ("en", "youtube_manual"))
        selected = select_subtitle_track({}, automatic, ("en",))
        self.assertEqual(selected[:2], ("en", "youtube_auto"))

    def test_vtt_normalization_cleans_markup_empty_and_rolling_duplicates(self) -> None:
        payload = """WEBVTT

00:00:00.000 --> 00:00:02.000
<c>Hello</c>

00:00:01.500 --> 00:00:03.000
Hello world

00:00:03.500 --> 00:00:04.000
<i>   </i>
"""
        segments = normalize_vtt(payload)
        self.assertEqual([item["text"] for item in segments], ["Hello", "world"])
        self.assertEqual(segments[0]["start"], 0.0)

    def test_json3_normalization_preserves_timing(self) -> None:
        payload = '{"events":[{"tStartMs":1200,"dDurationMs":800,"segs":[{"utf8":"Hi "},{"utf8":"there"}]}]}'
        segments = normalize_json3(payload)
        self.assertEqual(
            segments, [{"start": 1.2, "end": 2.0, "text": "Hi there"}]
        )

    def test_single_uses_cache_and_force_refetches(self) -> None:
        provider = FakeTranscriptProvider()
        service = TranscriptService(provider, self.repository)
        first = service.transcript("video-1", "en")
        second = service.transcript("video-1", "en")
        forced = service.transcript("video-1", "en", force=True)
        self.assertTrue(first.success)
        self.assertTrue(second.cached)
        self.assertFalse(forced.cached)
        self.assertEqual(provider.calls, ["video-1", "video-1"])
        self.assertEqual(self.repository.count_transcripts(), 1)

    def test_batch_continues_and_channel_limit_is_respected(self) -> None:
        provider = FakeTranscriptProvider(failures={"video-2"})
        service = TranscriptService(provider, self.repository)
        report = service.transcript_channel("UC123", limit=2, language="en")
        self.assertEqual((report.attempted, report.succeeded, report.failed), (2, 1, 1))
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("video-2", provider.calls)


if __name__ == "__main__":
    unittest.main()
