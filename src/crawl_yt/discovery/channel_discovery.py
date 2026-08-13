"""Provider boundary and orchestration for channel discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..database.models import Channel
from ..database.repository import ChannelRepository


@dataclass(slots=True)
class DiscoveryBatch:
    search_results: int
    channels: list[Channel]


class ChannelDiscoveryProvider(Protocol):
    def search(self, keyword: str, limit: int) -> DiscoveryBatch: ...


@dataclass(slots=True)
class DiscoveryReport:
    search_results: int
    unique_channels: int
    persisted: int
    duplicates: int
    channels: list[Channel]


class DiscoveryService:
    def __init__(
        self, provider: ChannelDiscoveryProvider, repository: ChannelRepository
    ) -> None:
        self.provider = provider
        self.repository = repository

    def discover(
        self, keyword: str, limit: int = 50, dry_run: bool = False
    ) -> DiscoveryReport:
        batch = self.provider.search(keyword, limit)
        unique = {channel.channel_id: channel for channel in batch.channels}
        result_duplicates = len(batch.channels) - len(unique)
        persisted = 0
        existing = 0
        if not dry_run:
            for channel in unique.values():
                if self.repository.upsert_channel(channel):
                    persisted += 1
                else:
                    existing += 1
        return DiscoveryReport(
            search_results=batch.search_results,
            unique_channels=len(unique),
            persisted=persisted,
            duplicates=result_duplicates + existing,
            channels=list(unique.values()),
        )
