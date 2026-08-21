"""Local-only orchestration for channel score refreshes."""

from __future__ import annotations

from dataclasses import dataclass, field

from .discovery.channel_scoring import ChannelScoringService
from .database.repository import VideoRepository


@dataclass(slots=True)
class ScoringBatchResult:
    channels_scored: int = 0
    scoring_failures: list[tuple[str, str]] = field(default_factory=list)


class ChannelScoringLifecycle:
    def __init__(self, repository, scorer: ChannelScoringService | None = None) -> None:
        self.repository = (
            repository
            if hasattr(repository, "get_channel_scoring_signals")
            else VideoRepository(repository.database_path)
        )
        self.scorer = scorer or ChannelScoringService(self.repository)

    def score_channel(self, channel_id: str):
        return self.scorer.score_channel(channel_id)

    def score_channels(
        self, channel_ids: list[str] | set[str] | tuple[str, ...]
    ) -> ScoringBatchResult:
        result = ScoringBatchResult()
        for channel_id in sorted(set(channel_ids)):
            try:
                self.score_channel(channel_id)
            except Exception as error:
                result.scoring_failures.append((channel_id, str(error)))
            else:
                result.channels_scored += 1
        return result

    def score_unscored_channels(self, limit: int) -> ScoringBatchResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        channels = self.repository.list_unscored_channels(limit)
        return self.score_channels([channel.channel_id for channel in channels])
