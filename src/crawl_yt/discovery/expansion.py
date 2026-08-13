"""Bounded, explainable discovery expansion using local signals."""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..database.models import DiscoveryQuery, DiscoveryRun
from ..database.repository import VideoRepository
from .channel_discovery import ChannelDiscoveryProvider, DiscoveryService
from .channel_scoring import ChannelScoringService

STOPWORDS = {
    "a", "an", "and", "channel", "for", "from", "in", "new", "of",
    "official", "on", "the", "to", "video", "with", "youtube",
}
SOURCE_PRIORITY = {"tag": 4, "channel_keyword": 3, "video_title": 2, "channel_title": 1}


def normalize_query(value: str) -> str:
    return " ".join(value.split()).casefold()


def useful_phrase(value: str) -> str | None:
    if "http://" in value.casefold() or "https://" in value.casefold():
        return None
    words = [
        word
        for word in re.findall(r"[a-z][a-z0-9'-]*", value.casefold())
        if word not in STOPWORDS
    ]
    if not 2 <= len(words) <= 6 or all(word.isdigit() for word in words):
        return None
    return " ".join(words)


@dataclass(frozen=True, slots=True)
class ExpansionCandidate:
    query: str
    depth: int
    source: str
    parent_query: str
    priority: float


class ExpansionPlanner:
    def __init__(self, repository: VideoRepository, max_per_channel: int = 3) -> None:
        self.repository = repository
        self.max_per_channel = max_per_channel

    def candidates(
        self, channel_id: str, score: float, depth: int, parent_query: str
    ) -> list[ExpansionCandidate]:
        inputs = self.repository.get_expansion_inputs(channel_id)
        sources = (
            ("tag", inputs["tags"]),
            ("channel_keyword", inputs["discovery_keywords"]),
            ("video_title", inputs["video_titles"]),
            ("channel_title", [inputs["channel_title"]]),
        )
        found: list[ExpansionCandidate] = []
        seen: set[str] = set()
        for source, values in sources:
            for value in values:
                phrase = useful_phrase(str(value))
                if phrase is None:
                    continue
                normalized = normalize_query(phrase)
                if normalized in seen or normalized == normalize_query(parent_query):
                    continue
                seen.add(normalized)
                found.append(
                    ExpansionCandidate(
                        normalized,
                        depth,
                        source,
                        parent_query,
                        score + SOURCE_PRIORITY[source],
                    )
                )
                if len(found) >= self.max_per_channel:
                    return found
        return found


@dataclass(slots=True)
class ExpansionReport:
    seed_keyword: str
    status: str
    run_id: int | None = None
    queries_executed: int = 0
    new_channels: int = 0
    existing_channels: int = 0
    max_depth_reached: int = 0
    generated_queries: list[str] = field(default_factory=list)
    top_channels: list[tuple[str, float, str]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


class DiscoveryExpansionService:
    def __init__(
        self,
        provider: ChannelDiscoveryProvider,
        repository: VideoRepository,
        score_threshold: float = 50,
        max_queries_per_channel: int = 3,
        max_generated_per_query: int = 5,
    ) -> None:
        self.repository = repository
        self.discovery = DiscoveryService(provider, repository)
        self.scoring = ChannelScoringService(repository)
        self.planner = ExpansionPlanner(repository, max_queries_per_channel)
        self.score_threshold = score_threshold
        self.max_generated_per_query = max_generated_per_query

    def expand(
        self,
        seed_keyword: str,
        *,
        max_depth: int,
        channel_budget: int,
        query_budget: int,
        results_per_query: int = 20,
        dry_run: bool = False,
    ) -> ExpansionReport:
        seed = " ".join(seed_keyword.split())
        if not seed:
            raise ValueError("seed keyword must not be empty")
        if min(max_depth, channel_budget, query_budget, results_per_query) < 1:
            raise ValueError("all expansion limits must be positive")
        if dry_run:
            return self._dry_run(seed, max_depth)

        now = datetime.now(timezone.utc)
        run = self.repository.create_discovery_run(
            DiscoveryRun(None, seed, now, None, "running", max_depth, channel_budget, query_budget)
        )
        report = ExpansionReport(seed, "running", run.id)
        frontier: list[tuple[float, int, ExpansionCandidate]] = []
        counter = 0
        seed_candidate = ExpansionCandidate(seed, 0, "seed", seed, float("inf"))
        heapq.heappush(frontier, (-seed_candidate.priority, counter, seed_candidate))
        queued = {normalize_query(seed)}
        successful_queries = 0
        discovered_scores: dict[str, tuple[str, float, str]] = {}

        try:
            while frontier and report.queries_executed < query_budget and report.new_channels < channel_budget:
                _, _, item = heapq.heappop(frontier)
                query_row = self.repository.add_discovery_query(
                    DiscoveryQuery(None, run.id, normalize_query(item.query), item.depth, item.parent_query if item.depth else None, item.source, "pending")
                )
                report.queries_executed += 1
                report.max_depth_reached = max(report.max_depth_reached, item.depth)
                remaining = channel_budget - report.new_channels
                try:
                    discovered = self.discovery.discover(
                        item.query,
                        results_per_query,
                        max_new_channels=remaining,
                    )
                except Exception as error:
                    self.repository.finish_discovery_query(query_row.id, "failed", 0, 0)
                    report.failures.append((item.query, str(error)))
                    continue
                successful_queries += 1
                report.new_channels += discovered.new_channels
                report.existing_channels += discovered.existing_channels
                self.repository.finish_discovery_query(
                    query_row.id,
                    "completed",
                    discovered.unique_channels_in_search,
                    discovered.new_channels,
                )
                generated: list[ExpansionCandidate] = []
                for channel in discovered.channels:
                    score = self.scoring.score_channel(channel.channel_id, now)
                    discovered_scores[channel.channel_id] = (
                        channel.title,
                        score.score,
                        score.tier,
                    )
                    if item.depth >= max_depth or score.score < self.score_threshold:
                        continue
                    generated.extend(
                        self.planner.candidates(
                            channel.channel_id, score.score, item.depth + 1, item.query
                        )
                    )
                generated.sort(key=lambda candidate: (-candidate.priority, candidate.query))
                added = 0
                for candidate in generated:
                    normalized = normalize_query(candidate.query)
                    if normalized in queued:
                        continue
                    queued.add(normalized)
                    counter += 1
                    heapq.heappush(frontier, (-candidate.priority, counter, candidate))
                    report.generated_queries.append(candidate.query)
                    added += 1
                    if added >= self.max_generated_per_query:
                        break
            if successful_queries == 0:
                report.status = "failed"
            elif report.new_channels >= channel_budget or (
                report.queries_executed >= query_budget and frontier
            ):
                report.status = "stopped_budget"
            else:
                report.status = "completed"
        except Exception as error:
            report.status = "failed"
            report.failures.append(("fatal", str(error)))
        self.repository.finish_discovery_run(
            run.id,
            report.status,
            report.new_channels,
            report.queries_executed,
            report.failures[-1][1] if report.status == "failed" and report.failures else None,
        )
        report.top_channels = sorted(
            discovered_scores.values(), key=lambda item: (-item[1], item[0])
        )[:10]
        return report

    def _dry_run(self, seed: str, max_depth: int) -> ExpansionReport:
        report = ExpansionReport(seed, "completed", generated_queries=[seed])
        if max_depth < 1:
            return report
        candidates: list[ExpansionCandidate] = []
        for channel_id in self.repository.list_channel_ids_for_keyword(seed):
            score = self.repository.get_channel_score(channel_id)
            if score and score.score >= self.score_threshold:
                candidates.extend(self.planner.candidates(channel_id, score.score, 1, seed))
        candidates.sort(key=lambda item: (-item.priority, item.query))
        report.generated_queries.extend(
            item.query for item in candidates[: self.max_generated_per_query]
        )
        return report
