"""Centralized crawl scheduling intervals."""

from __future__ import annotations

from datetime import timedelta

HIGH_INTERVAL = timedelta(days=3)
MEDIUM_INTERVAL = timedelta(days=7)
LOW_INTERVAL = timedelta(days=14)
UNSCORED_INTERVAL = timedelta(days=1)
FULL_REFRESH_INTERVAL = timedelta(days=14)


class CrawlPriorityPolicy:
    INTERVALS = {
        "high": HIGH_INTERVAL,
        "medium": MEDIUM_INTERVAL,
        "low": LOW_INTERVAL,
        "unscored": UNSCORED_INTERVAL,
    }

    def interval_for(self, tier: str | None) -> timedelta:
        return self.INTERVALS.get(tier or "unscored", UNSCORED_INTERVAL)


def failure_retry_interval(consecutive_failures: int) -> timedelta:
    return timedelta(days=min(3, max(1, int(consecutive_failures))))
