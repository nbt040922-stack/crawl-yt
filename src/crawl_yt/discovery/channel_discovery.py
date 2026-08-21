"""Provider boundary and orchestration for channel discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..database.models import Channel
from ..database.repository import ChannelRepository
from .normalization import normalize_discovery_keyword
from ..scoring_lifecycle import ChannelScoringLifecycle


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
    channels_scored: int = 0
    scoring_failures: list[tuple[str, str]] = field(default_factory=list)


class DiscoveryService:
    def __init__(
        self, provider: ChannelDiscoveryProvider, repository: ChannelRepository,
        scoring_lifecycle: ChannelScoringLifecycle | None = None,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.scoring_lifecycle = scoring_lifecycle or ChannelScoringLifecycle(repository)

    def discover(
        self,
        keyword: str,
        limit: int = 50,
        dry_run: bool = False,
        max_new_channels: int | None = None,
    ) -> DiscoveryReport:
        search_query = " ".join(keyword.split())
        provenance_keyword = normalize_discovery_keyword(search_query)
        if not provenance_keyword:
            raise ValueError("discovery keyword must not be empty")
        batch = self.provider.search(search_query, limit)
        unique = {channel.channel_id: channel for channel in batch.channels}
        new_channels = existing_channels = 0
        new_channel_ids: list[str] = []
        new_relationships = existing_relationships = 0
        processed: list[Channel] = []
        score_candidates: set[str] = set()

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
                    channel.channel_id, provenance_keyword, batch.source
                )
            else:
                is_new_channel = self.repository.upsert_channel(channel)
                is_new_relationship = self.repository.record_discovery(
                    channel.channel_id, provenance_keyword, batch.source
                )
            score_repository = getattr(self.scoring_lifecycle, "repository", self.repository)
            current_score = score_repository.get_channel_score(channel.channel_id) if hasattr(score_repository, "get_channel_score") else None
            if not dry_run and (is_new_channel or current_score is None or is_new_relationship):
                score_candidates.add(channel.channel_id)
            new_channels += is_new_channel
            processed.append(channel)
            if is_new_channel:
                new_channel_ids.append(channel.channel_id)
            existing_channels += not is_new_channel
            new_relationships += is_new_relationship
            existing_relationships += not is_new_relationship

        report = DiscoveryReport(
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
        if not dry_run:
            scoring = self.scoring_lifecycle.score_channels(score_candidates)
            report.channels_scored = scoring.channels_scored
            report.scoring_failures = scoring.scoring_failures
        return report
