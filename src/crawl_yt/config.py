"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


DISCOVERY_MAX_QUERIES = 8
DISCOVERY_PER_QUERY_BATCH_SIZE = 100
DISCOVERY_MIN_UNIQUE_CANDIDATES = 100
DISCOVERY_MAX_UNIQUE_CANDIDATES = 500


@dataclass(frozen=True, slots=True)
class Config:
    youtube_api_key: str | None
    database_url: str
    transcript_provider: str
    max_workers: int

    @classmethod
    def from_env(cls) -> "Config":
        max_workers = int(os.getenv("MAX_WORKERS", "4"))
        if max_workers < 1:
            raise ValueError("MAX_WORKERS must be a positive integer")
        return cls(
            youtube_api_key=os.getenv("YOUTUBE_API_KEY") or None,
            database_url=os.getenv("DATABASE_URL", "sqlite:///data/crawl_yt.db"),
            transcript_provider=os.getenv("TRANSCRIPT_PROVIDER", "ytdlp"),
            max_workers=max_workers,
        )
