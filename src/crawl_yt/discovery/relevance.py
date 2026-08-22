"""Deterministic, channel-level topic relevance evaluation."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
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
    matched_concepts: list[str] = field(default_factory=list)
    title_evidence: list["TitleTopicEvidence"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TitleTopicEvidence:
    title: str
    matched_concepts: list[str]


def normalize_topic_terms(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(str(value).split()).casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def build_effective_concepts(
    keyword: str,
    profile_concepts: Iterable[str] = (),
    extra_concepts: Iterable[str] = (),
) -> list[str]:
    return [
        concept
        for concept in normalize_topic_terms([keyword, *profile_concepts, *extra_concepts])
        if is_meaningful_topic_concept(concept)
    ]


def is_meaningful_topic_concept(value: str) -> bool:
    tokens = _tokens(value)
    return len(tokens) >= 2 or (len(tokens) == 1 and tokens[0] not in _GENERIC_TOKENS)


def get_topic_policy(mode: str) -> TopicPolicy:
    normalized = " ".join(str(mode).split()).casefold() or "balanced"
    if normalized not in _POLICIES:
        raise ValueError("mode must be strict, balanced, or broad")
    coverage, floor = _POLICIES[normalized]
    return TopicPolicy(normalized, coverage, floor)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w]+", " ".join(text.split()).casefold(), flags=re.UNICODE)


def _token_forms(token: str) -> set[str]:
    forms = {token}
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        forms.update((base, base + "e"))
    return forms


def _ordered_phrase_match(text_tokens: list[str], concept_tokens: list[str]) -> bool:
    width = len(concept_tokens)
    return any(
        all(
            _token_forms(concept) & _token_forms(token)
            for concept, token in zip(concept_tokens, text_tokens[start:start + width])
        )
        for start in range(len(text_tokens) - width + 1)
    )


def _conservative_reordered_match(
    text_tokens: list[str], concept_tokens: list[str]
) -> bool:
    if len(concept_tokens) < 3:
        return False
    width = len(concept_tokens) + 2
    for start in range(max(1, len(text_tokens) - width + 1)):
        window = text_tokens[start:start + width]
        remaining = list(window)
        for concept in concept_tokens:
            match = next((i for i, token in enumerate(remaining)
                          if _token_forms(concept) & _token_forms(token)), None)
            if match is None:
                break
            remaining.pop(match)
        else:
            if any(
                _ordered_phrase_match(window, concept_tokens[index:index + 2])
                for index in range(len(concept_tokens) - 1)
            ):
                return True
    return False


def match_topic_concepts(text: str, concepts: Iterable[str]) -> list[str]:
    text_tokens = _tokens(text)
    matched: list[str] = []
    for concept in normalize_topic_terms(concepts):
        concept_tokens = _tokens(concept)
        if not concept_tokens or (
            len(concept_tokens) == 1 and concept_tokens[0] in _GENERIC_TOKENS
        ):
            continue
        if _ordered_phrase_match(text_tokens, concept_tokens) or (
            _conservative_reordered_match(text_tokens, concept_tokens)
        ):
            matched.append(concept)
    return matched


def _identity(channel: Channel, phrases: list[str], query_tokens: list[str]) -> tuple[str, str]:
    text = " ".join(filter(None, [channel.title, channel.description])).strip()
    if not text:
        return "none", "none"
    if matched := match_topic_concepts(text, phrases):
        return "strong", "title/description: " + "; ".join(matched[:3])
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
    primary_tokens = _tokens(query)
    generic_single_primary = len(primary_tokens) == 1 and primary_tokens[0] in _GENERIC_TOKENS
    primary_phrase = [] if generic_single_primary else [query]
    phrases = normalize_topic_terms([*primary_phrase, *related])
    titles = list(recent_video_titles)[:DISCOVERY_TOPIC_SAMPLE_SIZE]
    title_evidence = [TitleTopicEvidence(title, match_topic_concepts(title, phrases)) for title in titles]
    matches = sum(bool(item.matched_concepts) for item in title_evidence)
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
    concept_counts = Counter(
        concept for item in title_evidence for concept in item.matched_concepts
    )
    matched_concepts = [concept for concept, _ in concept_counts.most_common()]
    return TopicEvidence(
        sample_size, matches, coverage, identity, identity_evidence, accepted,
        reason, matched_concepts, title_evidence,
    )
