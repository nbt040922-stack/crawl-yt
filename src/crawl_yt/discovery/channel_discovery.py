"""Provider boundary and orchestration for channel discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

from ..database.models import Channel
from ..database.repository import ChannelRepository
from ..scoring_lifecycle import ChannelScoringLifecycle
from .normalization import normalize_discovery_keyword
from .relevance import DISCOVERY_TOPIC_SAMPLE_SIZE, TopicEvidence, evaluate_channel_topic, normalize_topic_terms


@dataclass(slots=True)
class DiscoveryBatch:
    search_results: int
    channels: list[Channel]
    source: str


@dataclass(slots=True)
class ChannelVerification:
    channel: Channel
    recent_video_titles: list[str]


class ChannelDiscoveryProvider(Protocol):
    def search(self, keyword: str, limit: int) -> DiscoveryBatch: ...

    def verify(self, channel: Channel, sample_size: int = DISCOVERY_TOPIC_SAMPLE_SIZE) -> ChannelVerification: ...


@dataclass(slots=True)
class DiscoveryCandidate:
    channel: Channel
    evidence: TopicEvidence


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
    mode: str = "balanced"
    related_terms: list[str] = field(default_factory=list)
    candidate_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    accepted_candidates: list[DiscoveryCandidate] = field(default_factory=list)
    rejected_candidates: list[DiscoveryCandidate] = field(default_factory=list)
    target_accepted: int = 0
    maximum_candidates: int = 0

    def rejection_summary(self) -> dict[str, int]:
        summary = {
            "no_usable_sample": 0,
            "coverage_0_15": 0,
            "coverage_15_25": 0,
            "coverage_25_40": 0,
            "coverage_40_60": 0,
            "coverage_60_plus": 0,
            "verification_failed": 0,
        }
        for candidate in self.rejected_candidates:
            evidence = candidate.evidence
            if evidence.reason.startswith("verification_failed:"):
                summary["verification_failed"] += 1
                continue
            if evidence.sample_size == 0:
                summary["no_usable_sample"] += 1
            elif evidence.topic_coverage <= 0.15:
                summary["coverage_0_15"] += 1
            elif evidence.topic_coverage <= 0.25:
                summary["coverage_15_25"] += 1
            elif evidence.topic_coverage < 0.40:
                summary["coverage_25_40"] += 1
            elif evidence.topic_coverage < 0.60:
                summary["coverage_40_60"] += 1
            else:
                summary["coverage_60_plus"] += 1
        return summary


class DiscoveryService:
    def __init__(
        self, provider: ChannelDiscoveryProvider, repository: ChannelRepository,
        scoring_lifecycle: ChannelScoringLifecycle | None = None,
        enforce_topic_gate: bool = True,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.scoring_lifecycle = scoring_lifecycle or ChannelScoringLifecycle(repository)
        self.enforce_topic_gate = enforce_topic_gate

    def discover(
        self,
        keyword: str,
        limit: int = 50,
        dry_run: bool = False,
        max_new_channels: int | None = None,
        mode: str = "balanced",
        related_terms: Iterable[str] | str | None = None,
    ) -> DiscoveryReport:
        search_query = " ".join(keyword.split())
        provenance_keyword = normalize_discovery_keyword(search_query)
        if not provenance_keyword:
            raise ValueError("discovery keyword must not be empty")
        if limit < 1:
            raise ValueError("discovery limit must be positive")
        normalized_related = normalize_topic_terms(
            related_terms.split(",") if isinstance(related_terms, str) else (related_terms or [])
        )
        # Search beyond the requested accepted count, but keep the provider bounded.
        candidate_cap = min(1000, max(limit, limit * 5))
        batch = self.provider.search(search_query, candidate_cap)
        unique = {channel.channel_id: channel for channel in batch.channels}
        new_channels = existing_channels = 0
        new_channel_ids: list[str] = []
        new_relationships = existing_relationships = 0
        accepted: list[DiscoveryCandidate] = []
        rejected: list[DiscoveryCandidate] = []
        processed_channels: list[Channel] = []
        score_candidates: set[str] = set()
        inspected = 0

        for channel in unique.values():
            if inspected >= candidate_cap or len(accepted) >= limit:
                break
            inspected += 1
            if not self.enforce_topic_gate:
                verified_channel = channel
                evidence = TopicEvidence(0, 0, 0.0, "none", "legacy", True, "legacy discovery")
            else:
                try:
                    verification = self.provider.verify(channel, DISCOVERY_TOPIC_SAMPLE_SIZE)
                    verified_channel = verification.channel
                    evidence = evaluate_channel_topic(
                        verified_channel, search_query, normalized_related,
                        verification.recent_video_titles, mode,
                    )
                except Exception as error:
                    evidence = TopicEvidence(0, 0, 0.0, "none", "none", False, f"verification_failed: {error}")
                    verified_channel = channel
            candidate = DiscoveryCandidate(verified_channel, evidence)
            if not evidence.accepted:
                rejected.append(candidate)
                continue
            accepted.append(candidate)
            known = self.repository.get_channel(verified_channel.channel_id) is not None
            if max_new_channels is not None and not known and new_channels >= max_new_channels:
                continue
            if dry_run:
                is_new_channel = not known
                is_new_relationship = not self.repository.discovery_exists(
                    verified_channel.channel_id, provenance_keyword, batch.source
                )
            else:
                is_new_channel = self.repository.upsert_channel(verified_channel)
                is_new_relationship = self.repository.record_discovery(
                    verified_channel.channel_id, provenance_keyword, batch.source
                )
            processed_channels.append(verified_channel)
            score_repository = getattr(self.scoring_lifecycle, "repository", self.repository)
            current_score = score_repository.get_channel_score(verified_channel.channel_id) if hasattr(score_repository, "get_channel_score") else None
            if not dry_run and (is_new_channel or current_score is None or is_new_relationship):
                score_candidates.add(verified_channel.channel_id)
            new_channels += is_new_channel
            new_channel_ids.extend([verified_channel.channel_id] if is_new_channel else [])
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
            channels=processed_channels,
            new_channel_ids=new_channel_ids,
            mode=mode.casefold(),
            related_terms=normalized_related,
            candidate_count=inspected,
            accepted_count=len(accepted),
            rejected_count=len(rejected),
            accepted_candidates=accepted,
            rejected_candidates=rejected,
            target_accepted=limit,
            maximum_candidates=candidate_cap,
        )
        if not dry_run:
            scoring = self.scoring_lifecycle.score_channels(score_candidates)
            report.channels_scored = scoring.channels_scored
            report.scoring_failures = scoring.scoring_failures
        return report
