"""Cadence evidence and qualification for Discovery admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Iterable

MIN_DISCOVERY_VIDEOS_PER_WEEK = 3.0


class CadenceStatus(StrEnum):
    QUALIFIED = "qualified"
    BELOW_TARGET = "below_target"
    INSUFFICIENT_DATA = "insufficient_data"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CadenceRates:
    videos_per_week_30d: float | None
    videos_per_week_90d: float | None
    observation_span_days: int = 0


@dataclass(frozen=True, slots=True)
class CadenceEvidence:
    status: CadenceStatus
    videos_per_week: float | None
    band: str
    reason: str
    videos_per_week_30d: float | None = None
    videos_per_week_90d: float | None = None


@dataclass(frozen=True, slots=True)
class CadenceProbe:
    """Bounded date probe; exhausted means the provider returned all entries."""

    published_dates: tuple[datetime, ...]
    exhausted: bool


def cadence_band(videos_per_week: float | None) -> str:
    if videos_per_week is None:
        return "insufficient data"
    rate = float(videos_per_week)
    if rate < MIN_DISCOVERY_VIDEOS_PER_WEEK:
        return "below target"
    if rate < 5:
        return "good"
    if rate < 7:
        return "very good"
    return "excellent"


def evaluate_cadence(
    videos_per_week: float | None,
    *,
    videos_per_week_30d: float | None = None,
    videos_per_week_90d: float | None = None,
    failure: str | None = None,
) -> CadenceEvidence:
    if failure:
        return CadenceEvidence(
            CadenceStatus.FAILED, None, "failed", failure,
            videos_per_week_30d, videos_per_week_90d,
        )
    if videos_per_week is None:
        return CadenceEvidence(
            CadenceStatus.INSUFFICIENT_DATA, None, cadence_band(None),
            "Insufficient cadence data", videos_per_week_30d, videos_per_week_90d,
        )
    band = cadence_band(videos_per_week)
    if float(videos_per_week) < MIN_DISCOVERY_VIDEOS_PER_WEEK:
        return CadenceEvidence(
            CadenceStatus.BELOW_TARGET, float(videos_per_week), band,
            "Topic matched, but publishing cadence is below 3 videos/week.",
            videos_per_week_30d, videos_per_week_90d,
        )
    return CadenceEvidence(
        CadenceStatus.QUALIFIED, float(videos_per_week), band,
        "Cadence meets the 3 videos/week target.",
        videos_per_week_30d, videos_per_week_90d,
    )


def rates_from_dates(
    published_dates: Iterable[datetime], now: datetime | None = None
) -> CadenceRates:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    dates = []
    for value in published_dates:
        current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        dates.append(current.astimezone(timezone.utc))
    if not dates:
        return CadenceRates(None, None, 0)
    cutoff_30 = reference - timedelta(days=30)
    cutoff_90 = reference - timedelta(days=90)
    recent_30 = sum(value >= cutoff_30 for value in dates)
    recent_90 = sum(value >= cutoff_90 for value in dates)
    span = max(0, (max(dates) - min(dates)).days)
    return CadenceRates(
        recent_30 / (30 / 7),
        recent_90 / (90 / 7),
        span,
    )


def evidence_from_probe(
    probe: CadenceProbe, now: datetime | None = None
) -> CadenceEvidence:
    reference = now or datetime.now(timezone.utc)
    normalized = [
        value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        for value in probe.published_dates
    ]
    cutoff_30 = reference - timedelta(days=30)
    recent_30 = sum(value >= cutoff_30 for value in normalized)
    # A bounded probe is reliable once it reaches the 30-day boundary, is
    # exhausted, or has at least 30 recent entries (already >= 7/week).
    covered_window = probe.exhausted or any(value <= cutoff_30 for value in normalized) or recent_30 >= 30
    if not normalized or not covered_window:
        return evaluate_cadence(None)
    rates = rates_from_dates(normalized, reference)
    return evaluate_cadence(
        rates.videos_per_week_30d,
        videos_per_week_30d=rates.videos_per_week_30d,
        videos_per_week_90d=rates.videos_per_week_90d,
    )
