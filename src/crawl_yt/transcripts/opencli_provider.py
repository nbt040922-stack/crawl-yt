"""Optional OpenCLI transcript provider."""

from __future__ import annotations

import re
import shutil
import subprocess

from .errors import NoSubtitleError, ProviderUnavailableError
from .provider import TranscriptData


class OpenCliTranscriptProvider:
    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("opencli")

    @property
    def available(self) -> bool:
        return self.executable is not None

    def fetch(
        self,
        video_id: str,
        webpage_url: str | None,
        preferred_languages: tuple[str, ...],
    ) -> TranscriptData:
        if not self.executable:
            raise ProviderUnavailableError("opencli executable is not installed")
        url = webpage_url or f"https://www.youtube.com/watch?v={video_id}"
        command = [self.executable, "youtube", "transcript", url, "-f", "yaml"]
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=120
        )
        if result.returncode != 0:
            raise NoSubtitleError(result.stderr.strip() or "opencli transcript failed")
        text = self._plain_text(result.stdout)
        if not text:
            raise NoSubtitleError("opencli returned no transcript text")
        return TranscriptData(
            video_id=video_id,
            language=preferred_languages[0] if preferred_languages else "und",
            source="opencli",
            text=text,
            segments=[],
        )

    @staticmethod
    def _plain_text(payload: str) -> str:
        lines = []
        for line in payload.splitlines():
            value = re.sub(r"^\s*(?:-|text:|transcript:)\s*", "", line).strip()
            if value and not value.endswith(":"):
                lines.append(value.strip("\"'"))
        return " ".join(" ".join(lines).split())
