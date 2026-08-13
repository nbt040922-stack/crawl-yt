"""Opt-in local audio transcription with a replaceable ASR backend."""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from typing import Protocol

from yt_dlp import YoutubeDL

from .errors import ProviderUnavailableError, TranscriptionError
from .provider import TranscriptData


class LocalASRBackend(Protocol):
    def transcribe(
        self, audio_path: Path, requested_language: str | None = None
    ) -> tuple[str, list[dict[str, float | str]]]: ...


class FasterWhisperBackend:
    def __init__(self, model_name: str = "small") -> None:
        if importlib.util.find_spec("faster_whisper") is None:
            raise ProviderUnavailableError(
                "faster-whisper is not installed; run pip install -e .[asr]"
            )
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_name, device="cuda", compute_type="float16")

    def transcribe(
        self, audio_path: Path, requested_language: str | None = None
    ) -> tuple[str, list[dict[str, float | str]]]:
        segments, info = self.model.transcribe(
            str(audio_path), language=requested_language
        )
        normalized = [
            {"start": float(item.start), "end": float(item.end), "text": item.text.strip()}
            for item in segments
            if item.text.strip()
        ]
        return str(info.language or "und"), normalized


class LocalWhisperTranscriptProvider:
    def __init__(self, backend: LocalASRBackend | None = None) -> None:
        self.backend = backend

    @property
    def available(self) -> bool:
        return self.backend is not None or importlib.util.find_spec("faster_whisper") is not None

    def fetch(
        self,
        video_id: str,
        webpage_url: str | None,
        preferred_languages: tuple[str, ...],
    ) -> TranscriptData:
        backend = self.backend or FasterWhisperBackend()
        url = webpage_url or f"https://www.youtube.com/watch?v={video_id}"
        try:
            with tempfile.TemporaryDirectory(prefix="crawl-yt-audio-") as directory:
                target = str(Path(directory) / "audio.%(ext)s")
                options = {
                    "format": "bestaudio",
                    "noplaylist": True,
                    "outtmpl": target,
                    "quiet": True,
                }
                with YoutubeDL(options) as ydl:
                    ydl.extract_info(url, download=True)
                files = list(Path(directory).glob("audio.*"))
                if not files:
                    raise TranscriptionError("yt-dlp produced no audio file")
                requested = preferred_languages[0] if preferred_languages else None
                language, segments = backend.transcribe(files[0], requested)
                if not segments:
                    raise TranscriptionError("local ASR returned no segments")
                text = " ".join(str(item["text"]) for item in segments)
                return TranscriptData(
                    video_id, language, "local_whisper", text, segments
                )
        except (ProviderUnavailableError, TranscriptionError):
            raise
        except Exception as error:
            raise TranscriptionError(str(error)) from error
