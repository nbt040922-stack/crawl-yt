"""Provider boundary and orchestration for channel discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Protocol

from ..config import (
    DISCOVERY_MAX_QUERIES,
    DISCOVERY_MAX_UNIQUE_CANDIDATES,
    DISCOVERY_MIN_UNIQUE_CANDIDATES,
    DISCOVERY_PER_QUERY_BATCH_SIZE,
)
from ..database.models import Channel
from ..database.models import Video
from ..database.repository import ChannelRepository
from ..scoring_lifecycle import ChannelScoringLifecycle
from .normalization import normalize_discovery_keyword
from .cadence import (
    MIN_DISCOVERY_VIDEOS_PER_WEEK,
    CadenceEvidence,
    CadenceStatus,
    evaluate_cadence,
    rates_from_dates,
)
from .relevance import (
    DISCOVERY_TOPIC_SAMPLE_SIZE,
    TopicEvidence,
    build_effective_concepts,
    evaluate_channel_topic,
    get_topic_policy,
    normalize_topic_terms,
)


def build_discovery_query_plan(primary_query: str, search_concepts: Iterable[str]) -> list[str]:
    """Return normalized, case-insensitively unique queries with the primary first."""
    primary = " ".join(primary_query.split())
    if not primary:
        return []
    queries = [primary]
    seen = {normalize_discovery_keyword(primary)}
    for concept in search_concepts:
        query = " ".join(str(concept).split())
        normalized = normalize_discovery_keyword(query)
        if normalized and normalized not in seen:
            queries.append(query)
            seen.add(normalized)
        if len(queries) >= DISCOVERY_MAX_QUERIES:
            break
    return queries


def unique_candidate_budget(target_accepted: int) -> int:
    """Scale the unique-candidate ceiling while keeping discovery bounded."""
    return min(
        DISCOVERY_MAX_UNIQUE_CANDIDATES,
        max(DISCOVERY_MIN_UNIQUE_CANDIDATES, target_accepted * 25),
    )


@dataclass(slots=True)
class DiscoveryBatch:
    search_results: int
    channels: list[Channel]
    source: str


@dataclass(slots=True)
class ChannelVerification:
    channel: Channel
    recent_video_titles: list[str]
    recent_videos: list[Video] = field(default_factory=list)


class ChannelDiscoveryProvider(Protocol):
    def search(self, keyword: str, limit: int) -> DiscoveryBatch: ...

    def verify(self, channel: Channel, sample_size: int = DISCOVERY_TOPIC_SAMPLE_SIZE) -> ChannelVerification: ...


class InitialCrawlService(Protocol):
    def crawl(self, channel_id: str, *, full: bool = False): ...


@dataclass(slots=True)
class DiscoveryQueryMetric:
    query: str
    raw_results: int = 0
    unique_candidates: int = 0
    new_candidates: int = 0
    duplicate_candidates: int = 0
    failure: str | None = None


@dataclass(slots=True)
class DiscoveryCandidate:
    channel: Channel
    evidence: TopicEvidence
    discovered_by_queries: list[str] = field(default_factory=list)
    cadence: CadenceEvidence | None = None
    full_crawl_status: str = "not_run"
    final_status: str = "topic_rejected"


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
    coverage_threshold: float = 0.0
    minimum_distinct_concepts: int = 0
    identity_floor: float = 0.0
    topic_profile_id: int | None = None
    topic_profile_name: str | None = None
    effective_concepts: list[str] = field(default_factory=list)
    audit_run_id: int | None = None
    planned_queries: list[str] = field(default_factory=list)
    executed_queries: list[str] = field(default_factory=list)
    query_metrics: list[DiscoveryQueryMetric] = field(default_factory=list)
    cross_query_duplicates: int = 0
    topic_accepted_count: int = 0
    cadence_qualified_count: int = 0
    cadence_below_target_count: int = 0
    cadence_insufficient_count: int = 0
    cadence_failed_count: int = 0
    full_crawled_count: int = 0
    final_qualified_count: int = 0
    topic_accepted_candidates: list[DiscoveryCandidate] = field(default_factory=list)
    cadence_rejected_candidates: list[DiscoveryCandidate] = field(default_factory=list)
    cadence_insufficient_candidates: list[DiscoveryCandidate] = field(default_factory=list)
    final_failed_candidates: list[DiscoveryCandidate] = field(default_factory=list)

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
        initial_crawl_service: InitialCrawlService | None = None,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.scoring_lifecycle = scoring_lifecycle or ChannelScoringLifecycle(repository)
        self.enforce_topic_gate = enforce_topic_gate
        self.initial_crawl_service = initial_crawl_service

    @staticmethod
    def _cadence_for(verification: ChannelVerification | None) -> CadenceEvidence:
        if verification is None:
            return evaluate_cadence(None)
        dates = [
            video.published_at
            for video in verification.recent_videos
            if video.published_at is not None
        ]
        if not dates:
            return evaluate_cadence(None)
        rates = rates_from_dates(dates)
        return evaluate_cadence(
            rates.videos_per_week_30d,
            videos_per_week_30d=rates.videos_per_week_30d,
            videos_per_week_90d=rates.videos_per_week_90d,
        )

    def discover(
        self,
        keyword: str,
        limit: int = 50,
        dry_run: bool = False,
        max_new_channels: int | None = None,
        mode: str = "balanced",
        related_terms: Iterable[str] | str | None = None,
        topic_profile_id: int | None = None,
        extra_concepts: Iterable[str] | str | None = None,
        maximum_candidates: int | None = None,
    ) -> DiscoveryReport:
        search_query = " ".join(keyword.split())
        provenance_keyword = normalize_discovery_keyword(search_query)
        if not provenance_keyword:
            raise ValueError("discovery keyword must not be empty")
        if limit < 1:
            raise ValueError("discovery limit must be positive")
        policy = get_topic_policy(mode)
        profile = self.repository.get_topic_profile(topic_profile_id) if topic_profile_id is not None else None
        if topic_profile_id is not None and profile is None:
            raise ValueError("topic profile not found")
        normalized_related = normalize_topic_terms([
            *_topic_values(related_terms), *_topic_values(extra_concepts),
        ])
        effective_concepts = build_effective_concepts(
            search_query,
            profile.concept_phrases if profile else (),
            normalized_related,
        )
        if maximum_candidates is not None and maximum_candidates < 1:
            raise ValueError("maximum candidates must be positive")
        candidate_cap = (
            unique_candidate_budget(limit)
            if maximum_candidates is None
            else min(maximum_candidates, DISCOVERY_MAX_UNIQUE_CANDIDATES)
        )
        planned_queries = build_discovery_query_plan(
            search_query, profile.search_concepts if profile else (),
        )
        new_channels = existing_channels = 0
        new_channel_ids: list[str] = []
        new_relationships = existing_relationships = 0
        accepted: list[DiscoveryCandidate] = []
        rejected: list[DiscoveryCandidate] = []
        topic_accepted_candidates: list[DiscoveryCandidate] = []
        cadence_rejected_candidates: list[DiscoveryCandidate] = []
        cadence_insufficient_candidates: list[DiscoveryCandidate] = []
        final_failed_candidates: list[DiscoveryCandidate] = []
        processed_channels: list[Channel] = []
        score_candidates: set[str] = set()
        full_crawled_count = 0
        inspected = 0
        unique: dict[str, Channel] = {}
        candidates_by_id: dict[str, DiscoveryCandidate] = {}
        executed_queries: list[str] = []
        query_metrics: list[DiscoveryQueryMetric] = []
        search_results = duplicate_results = cross_query_duplicates = 0

        for query_index, query in enumerate(planned_queries):
            if len(accepted) >= limit or inspected >= candidate_cap:
                break
            request_limit = min(DISCOVERY_PER_QUERY_BATCH_SIZE, candidate_cap - inspected)
            executed_queries.append(query)
            try:
                batch = self.provider.search(query, request_limit)
            except Exception as error:
                if query_index == 0:
                    raise
                query_metrics.append(DiscoveryQueryMetric(
                    query, failure=str(error) or type(error).__name__,
                ))
                continue
            metric = DiscoveryQueryMetric(query, raw_results=batch.search_results)
            search_results += batch.search_results
            query_channel_ids: set[str] = set()

            for channel in batch.channels[:request_limit]:
                if len(accepted) >= limit or inspected >= candidate_cap:
                    break
                channel_id = channel.channel_id
                if channel_id in query_channel_ids:
                    metric.duplicate_candidates += 1
                    duplicate_results += 1
                    continue
                query_channel_ids.add(channel_id)
                metric.unique_candidates += 1
                if channel_id in unique:
                    metric.duplicate_candidates += 1
                    duplicate_results += 1
                    cross_query_duplicates += 1
                    candidates_by_id[channel_id].discovered_by_queries.append(query)
                    continue

                unique[channel_id] = channel
                metric.new_candidates += 1
                inspected += 1
                verification = None
                if not self.enforce_topic_gate:
                    verified_channel = channel
                    evidence = TopicEvidence(0, 0, 0.0, "none", "legacy", True, "legacy discovery")
                else:
                    try:
                        verification = self.provider.verify(channel, DISCOVERY_TOPIC_SAMPLE_SIZE)
                        verified_channel = verification.channel
                        evidence = evaluate_channel_topic(
                            verified_channel, search_query, effective_concepts,
                            verification.recent_video_titles, policy.mode,
                        )
                    except Exception as error:
                        evidence = TopicEvidence(0, 0, 0.0, "none", "none", False, f"verification_failed: {error}")
                        verified_channel = channel
                candidate = DiscoveryCandidate(verified_channel, evidence, [query])
                candidates_by_id[channel_id] = candidate
                if not evidence.accepted:
                    candidate.final_status = "topic_rejected"
                    rejected.append(candidate)
                    continue
                topic_accepted_candidates.append(candidate)
                candidate.final_status = "cadence_pending"
                cadence = (
                    evaluate_cadence(MIN_DISCOVERY_VIDEOS_PER_WEEK)
                    if not self.enforce_topic_gate
                    else self._cadence_for(verification)
                )
                candidate.cadence = cadence
                if cadence.status is CadenceStatus.BELOW_TARGET:
                    candidate.final_status = "cadence_rejected"
                    cadence_rejected_candidates.append(candidate)
                    continue
                if cadence.status is CadenceStatus.INSUFFICIENT_DATA:
                    candidate.final_status = "cadence_insufficient"
                    cadence_insufficient_candidates.append(candidate)
                    continue
                if cadence.status is CadenceStatus.FAILED:
                    candidate.final_status = "cadence_failed"
                    final_failed_candidates.append(candidate)
                    continue
                candidate.final_status = "cadence_qualified"
                known = self.repository.get_channel(verified_channel.channel_id) is not None
                if max_new_channels is not None and not known and new_channels >= max_new_channels:
                    continue
                if dry_run:
                    candidate.full_crawl_status = "dry_run"
                    candidate.final_status = "qualified"
                    accepted.append(candidate)
                    processed_channels.append(verified_channel)
                    continue
                if not known and self.initial_crawl_service is not None:
                    self.repository.upsert_channel(verified_channel)
                    try:
                        self.initial_crawl_service.crawl(
                            verified_channel.channel_id, full=True
                        )
                        candidate.full_crawl_status = "succeeded"
                        full_crawled_count += 1
                    except Exception as error:
                        candidate.full_crawl_status = "failed"
                        candidate.final_status = "failed"
                        candidate.cadence = evaluate_cadence(
                            cadence.videos_per_week,
                            videos_per_week_30d=cadence.videos_per_week_30d,
                            videos_per_week_90d=cadence.videos_per_week_90d,
                            failure=f"Initial Full Crawl failed: {error}",
                        )
                        final_failed_candidates.append(candidate)
                        continue
                elif not known:
                    candidate.full_crawl_status = "not_configured"
                else:
                    candidate.full_crawl_status = "not_required"
                candidate.final_status = "qualified"
                accepted.append(candidate)
                is_new_channel = not known
                if is_new_channel:
                    self.repository.upsert_channel(verified_channel)
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
            query_metrics.append(metric)

        report = DiscoveryReport(
            search_results=search_results,
            unique_channels_in_search=len(unique),
            duplicate_results_in_search=duplicate_results,
            new_channels=new_channels,
            existing_channels=existing_channels,
            new_discovery_relationships=new_relationships,
            existing_discovery_relationships=existing_relationships,
            channels=processed_channels,
            new_channel_ids=new_channel_ids,
            mode=policy.mode,
            related_terms=normalized_related,
            candidate_count=inspected,
            accepted_count=len(accepted),
            rejected_count=len(rejected),
            accepted_candidates=accepted,
            rejected_candidates=rejected,
            target_accepted=limit,
            maximum_candidates=candidate_cap,
            coverage_threshold=policy.coverage_threshold,
            minimum_distinct_concepts=policy.minimum_distinct_concepts,
            identity_floor=policy.identity_floor,
            topic_profile_id=profile.id if profile else None,
            topic_profile_name=profile.name if profile else None,
            effective_concepts=effective_concepts,
            planned_queries=planned_queries,
            executed_queries=executed_queries,
            query_metrics=query_metrics,
            cross_query_duplicates=cross_query_duplicates,
            topic_accepted_count=len(topic_accepted_candidates),
            cadence_qualified_count=sum(
                1 for item in topic_accepted_candidates
                if item.cadence is not None and item.cadence.status is CadenceStatus.QUALIFIED
            ),
            cadence_below_target_count=len(cadence_rejected_candidates),
            cadence_insufficient_count=len(cadence_insufficient_candidates),
            cadence_failed_count=sum(
                1 for item in final_failed_candidates
                if item.cadence is not None and item.cadence.status is CadenceStatus.FAILED
            ),
            full_crawled_count=full_crawled_count,
            final_qualified_count=len(accepted),
            topic_accepted_candidates=topic_accepted_candidates,
            cadence_rejected_candidates=cadence_rejected_candidates,
            cadence_insufficient_candidates=cadence_insufficient_candidates,
            final_failed_candidates=final_failed_candidates,
        )
        if not dry_run:
            scoring = self.scoring_lifecycle.score_channels(score_candidates)
            report.channels_scored = scoring.channels_scored
            report.scoring_failures = scoring.scoring_failures
        if not dry_run and self.enforce_topic_gate:
            report.audit_run_id = self.repository.create_discovery_relevance_run(
                keyword=provenance_keyword,
                mode=report.mode,
                target_accepted=limit,
                maximum_candidates=candidate_cap,
                profile_id=report.topic_profile_id,
                profile_name=report.topic_profile_name,
                effective_concepts=report.effective_concepts,
                planned_queries=report.planned_queries,
                executed_queries=report.executed_queries,
                query_metrics=[asdict(metric) for metric in report.query_metrics],
                summary={
                    "search_results": report.search_results,
                    "unique_channels": report.unique_channels_in_search,
                    "accepted": report.accepted_count,
                    "rejected": report.rejected_count,
                    "topic_accepted": report.topic_accepted_count,
                    "cadence_qualified": report.cadence_qualified_count,
                    "cadence_below_target": report.cadence_below_target_count,
                    "cadence_insufficient": report.cadence_insufficient_count,
                    "cadence_failed": report.cadence_failed_count,
                    "full_crawled": report.full_crawled_count,
                    "final_qualified": report.final_qualified_count,
                    "coverage_threshold": report.coverage_threshold,
                    "minimum_distinct_concepts": report.minimum_distinct_concepts,
                    "identity_floor": report.identity_floor,
                },
                candidate_evidence=(
                    [_candidate_payload(item, True) for item in accepted]
                    + [_candidate_payload(item, False) for item in rejected]
                    + [_candidate_payload(item, False) for item in cadence_rejected_candidates]
                    + [_candidate_payload(item, False) for item in cadence_insufficient_candidates]
                    + [_candidate_payload(item, False) for item in final_failed_candidates]
                ),
            )
        return report


def _topic_values(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [item for line in values.splitlines() for item in line.split(",")]
    return list(values)


def _candidate_payload(candidate: DiscoveryCandidate, accepted: bool) -> dict[str, object]:
    evidence = candidate.evidence
    return {
        "channel_id": candidate.channel.channel_id,
        "channel_title": candidate.channel.title,
        "channel_url": candidate.channel.channel_url,
        "accepted": accepted,
        "sample_size": evidence.sample_size,
        "topic_matches": evidence.topic_matches,
        "topic_coverage": evidence.topic_coverage,
        "distinct_matched_concepts": evidence.distinct_matched_concepts,
        "identity": evidence.identity,
        "reason": evidence.reason,
        "verification_status": "failed" if evidence.reason.startswith("verification_failed:") else "completed",
        "discovered_by_queries": list(candidate.discovered_by_queries),
        "cadence": (
            {
                "status": candidate.cadence.status.value,
                "videos_per_week": candidate.cadence.videos_per_week,
                "band": candidate.cadence.band,
                "reason": candidate.cadence.reason,
                "videos_per_week_30d": candidate.cadence.videos_per_week_30d,
                "videos_per_week_90d": candidate.cadence.videos_per_week_90d,
            }
            if candidate.cadence is not None else None
        ),
        "full_crawl_status": candidate.full_crawl_status,
        "final_status": candidate.final_status,
        "matched_concepts": evidence.matched_concepts,
        "title_evidence": [
            {"title": item.title, "matched_concepts": item.matched_concepts}
            for item in evidence.title_evidence
        ],
    }
