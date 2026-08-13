"""yt-dlp subtitle selection, retrieval, and normalization."""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL

from .provider import TranscriptData, select_language
from .errors import NoSubtitleError, TransientSubtitleError, UnavailableVideoError

_TAG = re.compile(r"<[^>]+>")
_TIMING = re.compile(
    r"(?P<start>\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+"
    r"(?P<end>\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}"
)


def _clean_text(value: str) -> str:
    value = html.unescape(_TAG.sub("", value)).replace("\u200b", "")
    return " ".join(value.split())


def _seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def deduplicate_segments(
    segments: list[dict[str, float | str]],
) -> list[dict[str, float | str]]:
    cleaned: list[dict[str, float | str]] = []
    for segment in sorted(segments, key=lambda item: float(item["start"])):
        text = _clean_text(str(segment["text"]))
        if not text:
            continue
        current = {
            "start": float(segment["start"]),
            "end": max(float(segment["end"]), float(segment["start"])),
            "text": text,
        }
        if not cleaned:
            cleaned.append(current)
            continue
        previous = cleaned[-1]
        overlaps = current["start"] <= float(previous["end"]) + 0.25
        previous_text = str(previous["text"])
        if overlaps and text == previous_text:
            previous["end"] = max(float(previous["end"]), float(current["end"]))
            continue
        if overlaps and text.startswith(f"{previous_text} "):
            current["text"] = text[len(previous_text) :].strip()
        elif overlaps and previous_text.startswith(f"{text} "):
            continue
        if current["text"]:
            cleaned.append(current)
    return cleaned


def normalize_vtt(payload: str) -> list[dict[str, float | str]]:
    blocks = re.split(r"\r?\n\s*\r?\n", payload.lstrip("\ufeff"))
    segments: list[dict[str, float | str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        timing_index = next(
            (index for index, line in enumerate(lines) if "-->" in line), None
        )
        if timing_index is None:
            continue
        match = _TIMING.search(lines[timing_index])
        if not match:
            continue
        start_text, end_text = match.group(0).split("-->")
        segments.append(
            {
                "start": _seconds(start_text.strip()),
                "end": _seconds(end_text.strip()),
                "text": " ".join(lines[timing_index + 1 :]),
            }
        )
    return deduplicate_segments(segments)


def normalize_json3(payload: str) -> list[dict[str, float | str]]:
    data = json.loads(payload)
    segments: list[dict[str, float | str]] = []
    for event in data.get("events", []):
        text = "".join(part.get("utf8", "") for part in event.get("segs", []))
        start_ms = event.get("tStartMs")
        if start_ms is None:
            continue
        duration_ms = event.get("dDurationMs", 0)
        segments.append(
            {
                "start": float(start_ms) / 1000,
                "end": (float(start_ms) + float(duration_ms)) / 1000,
                "text": text,
            }
        )
    return deduplicate_segments(segments)


def normalize_srv3(payload: str) -> list[dict[str, float | str]]:
    root = ET.fromstring(payload)
    segments: list[dict[str, float | str]] = []
    for element in root.iter():
        if element.tag.endswith("text"):
            start = float(element.attrib.get("start", 0))
            duration = float(element.attrib.get("dur", 0))
        elif element.tag.endswith("p") and "t" in element.attrib:
            start = float(element.attrib["t"]) / 1000
            duration = float(element.attrib.get("d", 0)) / 1000
        else:
            continue
        segments.append(
            {
                "start": start,
                "end": start + duration,
                "text": "".join(element.itertext()),
            }
        )
    return deduplicate_segments(segments)


def normalize_subtitle(payload: str, extension: str) -> list[dict[str, float | str]]:
    if extension == "json3":
        return normalize_json3(payload)
    if extension == "srv3":
        return normalize_srv3(payload)
    if extension == "vtt":
        return normalize_vtt(payload)
    raise ValueError(f"unsupported subtitle format: {extension}")


def select_subtitle_track(
    manual: dict[str, list[dict[str, Any]]],
    automatic: dict[str, list[dict[str, Any]]],
    preferred: tuple[str, ...],
) -> tuple[str, str, list[dict[str, Any]]] | None:
    for source, tracks in (
        ("youtube_manual", manual),
        ("youtube_auto", automatic),
    ):
        language = select_language(list(tracks), preferred)
        if language is not None:
            return language, source, tracks[language]
    return None


def _not_translated(track: dict[str, Any]) -> bool:
    return "tlang" not in parse_qs(urlsplit(str(track.get("url") or "")).query)


class YtDlpTranscriptProvider:
    def fetch(
        self,
        video_id: str,
        webpage_url: str | None,
        preferred_languages: tuple[str, ...],
    ) -> TranscriptData:
        options = {
            "noplaylist": True,
            "no_warnings": False,
            "quiet": True,
            "skip_download": True,
        }
        url = webpage_url or f"https://www.youtube.com/watch?v={video_id}"
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise UnavailableVideoError("yt-dlp returned no video metadata")
                manual = info.get("subtitles") or {}
                automatic = {
                    language: [track for track in tracks if _not_translated(track)]
                    for language, tracks in (info.get("automatic_captions") or {}).items()
                }
                automatic = {key: value for key, value in automatic.items() if value}
                selection = select_subtitle_track(
                    manual, automatic, preferred_languages
                )
                if selection is None:
                    raise NoSubtitleError(
                        "no matching manual or automatic subtitles"
                    )
                language, source, tracks = selection
                formats = {
                    track.get("ext"): track for track in tracks if track.get("url")
                }
                extension = next(
                    (item for item in ("json3", "vtt", "srv3") if item in formats),
                    None,
                )
                if extension is None:
                    raise NoSubtitleError(
                        "no supported timestamped subtitle format"
                    )
                try:
                    response = ydl.urlopen(formats[extension]["url"]).read()
                except HTTPError as error:
                    if error.code in {408, 425, 429} or error.code >= 500:
                        raise TransientSubtitleError(
                            f"temporary subtitle HTTP {error.code}"
                        ) from error
                    raise NoSubtitleError(f"subtitle HTTP {error.code}") from error
                except (OSError, URLError) as error:
                    raise TransientSubtitleError(str(error)) from error
                if not response:
                    raise TransientSubtitleError("empty subtitle response")
                payload = response.decode("utf-8", errors="replace")
        except (NoSubtitleError, TransientSubtitleError, UnavailableVideoError):
            raise
        except Exception as error:
            raise UnavailableVideoError(str(error)) from error
        try:
            segments = normalize_subtitle(payload, extension)
        except (ValueError, json.JSONDecodeError, ET.ParseError) as error:
            raise TransientSubtitleError(f"malformed subtitle: {error}") from error
        if not segments:
            raise TransientSubtitleError("subtitle contains no usable cues")
        text = " ".join(str(segment["text"]) for segment in segments)
        return TranscriptData(video_id, language, source, text, segments)
