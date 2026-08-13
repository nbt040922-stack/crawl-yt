"""Network-free fallback pipeline and local audio provider tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.crawl_yt.cli import main
from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import TranscriptRepository
from src.crawl_yt.transcripts.errors import (
    NoSubtitleError,
    ProviderUnavailableError,
    TranscriptionError,
    TransientSubtitleError,
    UnavailableVideoError,
)
from src.crawl_yt.transcripts.opencli_provider import OpenCliTranscriptProvider
from src.crawl_yt.transcripts.provider import (
    TranscriptData,
    TranscriptPipeline,
    TranscriptService,
)
from src.crawl_yt.transcripts.whisper_provider import LocalWhisperTranscriptProvider


def transcript_data(video_id: str, source: str = "youtube_manual") -> TranscriptData:
    segment = {"start": 0.0, "end": 1.0, "text": "hello"}
    return TranscriptData(video_id, "en", source, "hello", [segment])


class ScriptedProvider:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def fetch(self, video_id, webpage_url, preferred_languages):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = TranscriptRepository(Path(self.temp.name) / "test.db")
        self.repository.upsert_channel(Channel("UC1", "One"))
        for video_id in ("v1", "v2"):
            self.repository.upsert_video(
                Video(video_id, "UC1", video_id, datetime.now(timezone.utc))
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def pipeline(self, youtube, opencli=None, whisper=None) -> TranscriptPipeline:
        return TranscriptPipeline(youtube, self.repository, opencli, whisper)

    def test_manual_or_auto_success_stops_fallback(self) -> None:
        for source in ("youtube_manual", "youtube_auto"):
            youtube = ScriptedProvider(transcript_data("v1", source))
            opencli = ScriptedProvider(transcript_data("v1", "opencli"))
            whisper = ScriptedProvider(transcript_data("v1", "local_whisper"))
            result = self.pipeline(youtube, opencli, whisper).fetch(
                "v1", None, ("en",), fallback=True, allow_audio=True
            )
            self.assertEqual(result.source, source)
            self.assertEqual((youtube.calls, opencli.calls, whisper.calls), (1, 0, 0))

    def test_fallback_order_and_attempt_history(self) -> None:
        youtube = ScriptedProvider(NoSubtitleError("none"))
        opencli = ScriptedProvider(ProviderUnavailableError("missing"))
        whisper = ScriptedProvider(transcript_data("v1", "local_whisper"))
        pipeline = self.pipeline(youtube, opencli, whisper)
        result = pipeline.fetch("v1", None, ("en",), True, True)
        self.assertEqual(result.source, "local_whisper")
        attempts = self.repository.list_transcript_attempts("v1")
        self.assertEqual([item.provider for item in attempts], ["yt-dlp", "opencli", "local_whisper"])
        self.assertEqual([item.status for item in attempts], ["failed", "unavailable", "success"])

    def test_transient_retries_three_times_then_falls_back(self) -> None:
        youtube = ScriptedProvider(
            TransientSubtitleError("one"),
            TransientSubtitleError("two"),
            TransientSubtitleError("three"),
        )
        opencli = ScriptedProvider(transcript_data("v1", "opencli"))
        result = self.pipeline(youtube, opencli).fetch("v1", None, ("en",), True)
        self.assertEqual(result.source, "opencli")
        self.assertEqual((youtube.calls, opencli.calls), (3, 1))

    def test_permanent_failure_is_not_retried(self) -> None:
        youtube = ScriptedProvider(NoSubtitleError("none"))
        with self.assertRaises(NoSubtitleError):
            self.pipeline(youtube).fetch("v1", None, ("en",))
        self.assertEqual(youtube.calls, 1)

    def test_unavailable_video_stops_all_fallbacks(self) -> None:
        youtube = ScriptedProvider(UnavailableVideoError("private"))
        opencli = ScriptedProvider(transcript_data("v1", "opencli"))
        whisper = ScriptedProvider(transcript_data("v1", "local_whisper"))
        with self.assertRaises(UnavailableVideoError):
            self.pipeline(youtube, opencli, whisper).fetch(
                "v1", None, ("en",), fallback=True, allow_audio=True
            )
        self.assertEqual((youtube.calls, opencli.calls, whisper.calls), (1, 0, 0))

    def test_missing_opencli_does_not_mask_caption_failure(self) -> None:
        youtube = ScriptedProvider(NoSubtitleError("no captions"))
        opencli = ScriptedProvider(ProviderUnavailableError("missing"))
        with self.assertRaisesRegex(NoSubtitleError, "no captions"):
            self.pipeline(youtube, opencli).fetch("v1", None, ("en",), True)

    def test_audio_requires_fallback_and_explicit_permission(self) -> None:
        for fallback, allow_audio in ((False, False), (True, False), (False, True)):
            youtube = ScriptedProvider(NoSubtitleError("none"))
            whisper = ScriptedProvider(transcript_data("v1", "local_whisper"))
            with self.assertRaises(NoSubtitleError):
                self.pipeline(youtube, whisper=whisper).fetch(
                    "v1", None, ("en",), fallback, allow_audio
                )
            self.assertEqual(whisper.calls, 0)

    def test_cache_blocks_fallback_until_force_and_no_duplicate_rows(self) -> None:
        first = ScriptedProvider(transcript_data("v1", "youtube_manual"))
        service = TranscriptService(self.pipeline(first), self.repository)
        self.assertTrue(service.transcript("v1").success)
        second = ScriptedProvider(transcript_data("v1", "youtube_manual"))
        service = TranscriptService(self.pipeline(second), self.repository)
        self.assertTrue(service.transcript("v1", fallback=True).cached)
        self.assertEqual(second.calls, 0)
        self.assertTrue(service.transcript("v1", force=True).success)
        self.assertEqual(second.calls, 1)
        self.assertEqual(self.repository.count_transcripts(), 1)

    def test_batch_failure_does_not_stop_next_video(self) -> None:
        provider = ScriptedProvider(NoSubtitleError("none"), transcript_data("v2"))
        report = TranscriptService(provider, self.repository).transcript_pending(2, "en")
        self.assertEqual((report.attempted, report.succeeded, report.failed), (2, 1, 1))


class ProviderTests(unittest.TestCase):
    @patch("src.crawl_yt.transcripts.opencli_provider.subprocess.run")
    def test_opencli_success_has_no_invented_segments(self, run) -> None:
        run.return_value = SimpleNamespace(returncode=0, stdout="transcript: hello world\n", stderr="")
        result = OpenCliTranscriptProvider("opencli").fetch("v1", None, ("en",))
        self.assertEqual((result.source, result.text, result.segments), ("opencli", "hello world", []))

    def test_opencli_unavailable_is_typed(self) -> None:
        with self.assertRaises(ProviderUnavailableError):
            OpenCliTranscriptProvider().fetch("v1", None, ("en",))

    def test_audio_temp_file_cleanup_success_and_detected_language(self) -> None:
        seen: list[Path] = []

        class Backend:
            def transcribe(self, path, requested_language=None):
                seen.append(path)
                self.assert_requested = requested_language
                return "vi", [{"start": 1.0, "end": 2.5, "text": "xin chao"}]

        backend = Backend()
        with patch("src.crawl_yt.transcripts.whisper_provider.YoutubeDL", _fake_ytdl):
            result = LocalWhisperTranscriptProvider(backend).fetch("v1", None, ("en",))
        self.assertEqual((result.language, result.source), ("vi", "local_whisper"))
        self.assertEqual(result.segments[0]["start"], 1.0)
        self.assertEqual(backend.assert_requested, "en")
        self.assertFalse(seen[0].exists())

    def test_audio_temp_file_cleanup_failure(self) -> None:
        seen: list[Path] = []

        class Backend:
            def transcribe(self, path, requested_language=None):
                seen.append(path)
                raise RuntimeError("ASR failed")

        with patch("src.crawl_yt.transcripts.whisper_provider.YoutubeDL", _fake_ytdl):
            with self.assertRaises(TranscriptionError):
                LocalWhisperTranscriptProvider(Backend()).fetch("v1", None, ("en",))
        self.assertFalse(seen[0].exists())

    def test_allow_audio_requires_fallback_at_cli_boundary(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["transcript", "v1", "--allow-audio"])
        self.assertEqual(code, 2)
        self.assertIn("requires --fallback", stderr.getvalue())


class _FakeYDL:
    def __init__(self, options) -> None:
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=True):
        Path(self.options["outtmpl"].replace("%(ext)s", "webm")).write_bytes(b"audio")
        return {"id": "v1"}


def _fake_ytdl(options):
    return _FakeYDL(options)


if __name__ == "__main__":
    unittest.main()
