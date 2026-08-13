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
    source: str


class ChannelDiscoveryProvider(Protocol):
    def search(self, keyword: str, limit: int) -> DiscoveryBatch: ...


@dataclass(slots=True)
class DiscoveryReport:
    search_results: int
    unique_channels_in_search: int
    duplicate_results_in_search: int
    new_channels: int
    existing_channels: int
    new_discovery_relationships: int
    existing_discovery_relationships: int
    channels: list[Channel]
    new_channel_ids: list[str]


class DiscoveryService:
    def __init__(
        self, provider: ChannelDiscoveryProvider, repository: ChannelRepository
    ) -> None:
        self.provider = provider
        self.repository = repository

    def discover(
        self,
        keyword: str,
        limit: int = 50,
        dry_run: bool = False,
        max_new_channels: int | None = None,
    ) -> DiscoveryReport:
        batch = self.provider.search(keyword, limit)
        unique = {channel.channel_id: channel for channel in batch.channels}
        new_channels = existing_channels = 0
        new_channel_ids: list[str] = []
        new_relationships = existing_relationships = 0
        processed: list[Channel] = []

        for channel in unique.values():
            known = self.repository.get_channel(channel.channel_id) is not None
            if (
                not known
                and max_new_channels is not None
                and new_channels >= max_new_channels
            ):
                continue
            if dry_run:
                is_new_channel = not known
                is_new_relationship = not self.repository.discovery_exists(
                    channel.channel_id, keyword, batch.source
                )
            else:
                is_new_channel = self.repository.upsert_channel(channel)
                is_new_relationship = self.repository.record_discovery(
                    channel.channel_id, keyword, batch.source
                )
            new_channels += is_new_channel
            processed.append(channel)
            if is_new_channel:
                new_channel_ids.append(channel.channel_id)
            existing_channels += not is_new_channel
            new_relationships += is_new_relationship
            existing_relationships += not is_new_relationship

        return DiscoveryReport(
            search_results=batch.search_results,
            unique_channels_in_search=len(unique),
            duplicate_results_in_search=len(batch.channels) - len(unique),
            new_channels=new_channels,
            existing_channels=existing_channels,
            new_discovery_relationships=new_relationships,
            existing_discovery_relationships=existing_relationships,
            channels=processed,
            new_channel_ids=new_channel_ids,
        )
