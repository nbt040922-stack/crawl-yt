"""Deterministic, channel-level topic relevance evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..database.models import Channel

DISCOVERY_TOPIC_SAMPLE_SIZE = 20
MAX_CANDIDATE_MULTIPLIER = 5

_POLICIES = {
    "strict": (0.60, 0.40),
    "balanced": (0.40, 0.25),
    "broad": (0.25, 0.15),
}
_GENERIC_TOKENS = {"aging", "reality", "money", "life", "health", "news", "people", "living"}


@dataclass(frozen=True, slots=True)
class TopicPolicy:
    mode: str
    coverage_threshold: float
    identity_floor: float


@dataclass(frozen=True, slots=True)
class TopicEvidence:
    sample_size: int
    topic_matches: int
    topic_coverage: float
    identity: str
    identity_evidence: str
    accepted: bool
    reason: str


def normalize_topic_terms(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(str(value).split()).casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def get_topic_policy(mode: str) -> TopicPolicy:
    normalized = " ".join(str(mode).split()).casefold() or "balanced"
    if normalized not in _POLICIES:
        raise ValueError("mode must be strict, balanced, or broad")
    coverage, floor = _POLICIES[normalized]
    return TopicPolicy(normalized, coverage, floor)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w]+", " ".join(text.split()).casefold(), flags=re.UNICODE)


def _matches(text: str, phrases: list[str], query_tokens: list[str]) -> bool:
    normalized = " ".join(text.split()).casefold()
    if any(phrase in normalized for phrase in phrases):
        return True
    meaningful = [token for token in query_tokens if token not in _GENERIC_TOKENS]
    if len(meaningful) >= 2 and all(token in _tokens(normalized) for token in meaningful):
        return True
    if len(meaningful) == 1 and len(query_tokens) == 1:
        return meaningful[0] in _tokens(normalized)
    return False


def _identity(channel: Channel, phrases: list[str], query_tokens: list[str]) -> tuple[str, str]:
    text = " ".join(filter(None, [channel.title, channel.description])).strip()
    if not text:
        return "none", "none"
    if any(phrase in " ".join(text.split()).casefold() for phrase in phrases):
        return "strong", "title/description phrase"
    meaningful = [token for token in query_tokens if token not in _GENERIC_TOKENS]
    hits = sum(token in _tokens(text) for token in meaningful)
    if len(meaningful) >= 2 and hits == len(meaningful):
        return "strong", "title/description terms"
    if hits:
        return "moderate", "title/description term"
    return "none", "none"


def evaluate_channel_topic(
    channel: Channel,
    keyword: str,
    related_terms: Iterable[str],
    recent_video_titles: Iterable[str],
    mode: str = "balanced",
) -> TopicEvidence:
    policy = get_topic_policy(mode)
    query = " ".join(str(keyword).split()).casefold()
    query_tokens = _tokens(query)
    related = normalize_topic_terms(related_terms)
    phrases = normalize_topic_terms([query, *related])
    titles = list(recent_video_titles)[:DISCOVERY_TOPIC_SAMPLE_SIZE]
    matches = sum(_matches(title, phrases, query_tokens) for title in titles)
    sample_size = len(titles)
    coverage = matches / sample_size if sample_size else 0.0
    identity, identity_evidence = _identity(channel, phrases, query_tokens)
    accepted = coverage >= policy.coverage_threshold or (
        identity == "strong" and coverage >= policy.identity_floor
    )
    if accepted:
        reason = "coverage threshold met" if coverage >= policy.coverage_threshold else "strong identity exception"
    elif sample_size == 0:
        reason = "verification returned no recent video titles"
    elif identity == "strong" and coverage < policy.identity_floor:
        reason = "channel identity matched but recent video coverage is too low"
    else:
        reason = "topic appears only in isolated or insufficient recent videos"
    return TopicEvidence(sample_size, matches, coverage, identity, identity_evidence, accepted, reason)
