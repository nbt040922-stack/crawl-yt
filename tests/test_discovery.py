"""Network-free discovery normalization and service tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import ChannelRepository, VideoRepository
from src.crawl_yt.discovery.channel_discovery import (
    ChannelVerification,
    DiscoveryBatch,
    DiscoveryCandidate,
    DiscoveryQueryMetric,
    DiscoveryService,
    build_discovery_query_plan,
    unique_candidate_budget,
)
from src.crawl_yt.discovery.relevance import TopicEvidence
from src.crawl_yt.discovery.channel_scoring import ChannelScoringService
from src.crawl_yt.discovery.ytdlp_provider import channel_videos_url, normalize_channel


def _dated_videos(channel: Channel, count: int = 20) -> list[Video]:
    count = min(count, 19)
    now = datetime.now(timezone.utc)
    return [Video(f"{channel.channel_id}-{index}", channel.channel_id, "sample", now, published_at=now - timedelta(days=index)) for index in range(count)]


class FakeProvider:
    def search(self, keyword: str, limit: int) -> DiscoveryBatch:
        self.keyword = keyword
        return DiscoveryBatch(
            search_results=2,
            channels=[Channel("UC123", "Example"), Channel("UC123", "Example")],
            source="test",
        )

    def verify(self, channel, sample_size=20):
        return ChannelVerification(channel, [f"{self.keyword} planning"] * sample_size, _dated_videos(channel, sample_size))


class DiscoveryTests(unittest.TestCase):
    def _provider(self, channels, matches=20, fail_ids=None):
        class Provider:
            def __init__(self):
                self.verify_calls = []

            def search(self, keyword, limit):
                self.keyword = keyword
                return DiscoveryBatch(len(channels), channels, "test")

            def verify(self, channel, sample_size=20):
                self.verify_calls.append(channel.channel_id)
                if fail_ids and channel.channel_id in fail_ids:
                    raise RuntimeError("verification unavailable")
                count = matches[channel.channel_id] if isinstance(matches, dict) else matches
                titles = [f"{self.keyword} planning"] * count + ["Unrelated gardening"] * (sample_size - count)
                return ChannelVerification(channel, titles, _dated_videos(channel, sample_size))

        return Provider()

    def _multi_query_provider(self, batches, matches=20):
        class Provider:
            def __init__(self):
                self.search_calls = []
                self.verify_calls = []

            def search(self, keyword, limit):
                self.search_calls.append((keyword, limit))
                channels = batches[keyword]
                return DiscoveryBatch(len(channels), channels, "fake")

            def verify(self, channel, sample_size=20):
                self.verify_calls.append(channel.channel_id)
                count = matches[channel.channel_id] if isinstance(matches, dict) else matches
                titles = ["Retirement planning advice"] * count
                titles += ["Unrelated gardening"] * (sample_size - count)
                return ChannelVerification(channel, titles, _dated_videos(channel, sample_size))

        return Provider()

    def _profile(self, repository, *search_concepts):
        return repository.create_topic_profile(
            "Retirement", "", ["retirement planning"], list(search_concepts),
        )

    def test_query_plan_keeps_primary_query_first(self) -> None:
        plan = build_discovery_query_plan("  Retirement   Planning  ", ["solo retirement"])

        self.assertEqual(plan, ["Retirement Planning", "solo retirement"])

    def test_query_plan_removes_normalized_duplicates(self) -> None:
        plan = build_discovery_query_plan(
            "Retirement Planning",
            [" retirement   planning ", "Social Security", "social   security"],
        )

        self.assertEqual(plan, ["Retirement Planning", "Social Security"])

    def test_query_plan_caps_secondary_search_concepts_at_seven(self) -> None:
        plan = build_discovery_query_plan("retirement", [f"concept {number}" for number in range(10)])

        self.assertEqual(plan, ["retirement", *[f"concept {number}" for number in range(7)]])

    def test_unique_candidate_budget_clamps_target_multiplier(self) -> None:
        self.assertEqual(unique_candidate_budget(1), 100)
        self.assertEqual(unique_candidate_budget(10), 250)
        self.assertEqual(unique_candidate_budget(100), 500)

    def test_query_metrics_and_candidate_provenance_are_typed(self) -> None:
        metric = DiscoveryQueryMetric("retirement", raw_results=10, unique_candidates=8, duplicate_candidates=2)
        candidate = DiscoveryCandidate(
            Channel("UC1", "Candidate"),
            TopicEvidence(0, 0, 0.0, "none", "legacy", True, "legacy discovery"),
            discovered_by_queries=["retirement", "solo retirement"],
        )

        self.assertEqual(metric.duplicate_candidates, 2)
        self.assertEqual(candidate.discovered_by_queries, ["retirement", "solo retirement"])

    def test_multi_query_discovery_deduplicates_a_through_h_and_records_provenance(self) -> None:
        channels = {letter: Channel(f"UC{letter}", f"Candidate {letter}") for letter in "ABCDEFGH"}
        batches = {
            "retirement": [channels[letter] for letter in "ABCD"],
            "retirement income": [channels[letter] for letter in "CDEF"],
            "social security": [channels[letter] for letter in "FGH"],
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = self._profile(repository, "retirement income", "social security")
            provider = self._multi_query_provider(batches)

            report = DiscoveryService(provider, repository).discover(
                "retirement", 8, topic_profile_id=profile.id,
            )

            self.assertEqual(provider.search_calls, [
                ("retirement", 100),
                ("retirement income", 100),
                ("social security", 100),
            ])
            self.assertEqual(provider.verify_calls, ["UCC", "UCD", "UCF", "UCA", "UCB", "UCE", "UCG", "UCH"])
            self.assertEqual(report.search_results, 11)
            self.assertEqual(report.unique_channels_in_search, 8)
            self.assertEqual(report.duplicate_results_in_search, 3)
            self.assertEqual(report.cross_query_duplicates, 3)
            self.assertEqual(report.planned_queries, list(batches))
            self.assertEqual(report.executed_queries, list(batches))
            self.assertEqual(
                [
                    (metric.raw_results, metric.unique_candidates, metric.new_candidates, metric.duplicate_candidates)
                    for metric in report.query_metrics
                ],
                [(4, 4, 4, 0), (4, 4, 2, 2), (3, 3, 2, 1)],
            )
            candidates = {
                candidate.channel.channel_id: candidate
                for candidate in report.accepted_candidates + report.rejected_candidates
            }
            self.assertEqual(candidates["UCC"].discovered_by_queries, ["retirement", "retirement income"])
            self.assertEqual(candidates["UCF"].discovered_by_queries, ["retirement income", "social security"])
            self.assertEqual([channel.channel_id for channel in report.channels], ["UCC", "UCD", "UCF", "UCA", "UCB", "UCE", "UCG", "UCH"])

    def test_secondary_queries_contribute_candidates_and_primary_is_only_persisted_keyword(self) -> None:
        channels = {letter: Channel(f"UC{letter}", f"Candidate {letter}") for letter in "ABCD"}
        batches = {
            "retirement": [channels["A"], channels["B"]],
            "retirement income": [channels["C"], channels["D"]],
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = self._profile(repository, "retirement income")
            provider = self._multi_query_provider(batches)

            report = DiscoveryService(provider, repository).discover(
                "retirement", 4, topic_profile_id=profile.id,
            )

            self.assertEqual([channel.channel_id for channel in report.channels], ["UCA", "UCB", "UCC", "UCD"])
            self.assertEqual(repository.count_discovery_relationships(), 4)
            for channel_id in ("UCA", "UCB", "UCC", "UCD"):
                self.assertTrue(repository.discovery_exists(channel_id, "retirement", "fake"))
                self.assertFalse(repository.discovery_exists(channel_id, "retirement income", "fake"))

    def test_discovery_stops_before_secondary_query_when_primary_reaches_target(self) -> None:
        channels = [Channel(f"UC{letter}", f"Candidate {letter}") for letter in "ABCD"]
        batches = {
            "retirement": channels,
            "retirement income": [Channel("UCE", "Candidate E")],
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = self._profile(repository, "retirement income")
            provider = self._multi_query_provider(batches)

            report = DiscoveryService(provider, repository).discover(
                "retirement", 2, topic_profile_id=profile.id,
            )

            self.assertEqual(provider.search_calls, [("retirement", 100), ("retirement income", 96)])
            self.assertEqual(provider.verify_calls, ["UCA", "UCB"])
            self.assertEqual(report.executed_queries, ["retirement", "retirement income"])
            self.assertEqual(report.candidate_count, 5)

    def test_unique_candidate_override_limits_requests_and_inspection(self) -> None:
        channels = {letter: Channel(f"UC{letter}", f"Candidate {letter}") for letter in "ABCDEFG"}
        batches = {
            "retirement": [channels[letter] for letter in "ABC"],
            "retirement income": [channels[letter] for letter in "DEFG"],
            "social security": [Channel("UCH", "Candidate H")],
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = self._profile(repository, "retirement income", "social security")
            provider = self._multi_query_provider(batches, matches=0)

            report = DiscoveryService(provider, repository).discover(
                "retirement", 10, topic_profile_id=profile.id, maximum_candidates=5,
            )

            self.assertEqual(provider.search_calls, [("retirement", 5), ("retirement income", 2)])
            self.assertEqual(provider.verify_calls, ["UCA", "UCB", "UCC", "UCD", "UCE"])
            self.assertEqual(report.maximum_candidates, 5)
            self.assertEqual(report.candidate_count, 5)
            self.assertEqual(report.executed_queries, ["retirement", "retirement income"])

    def test_unique_candidate_override_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")

            with self.assertRaisesRegex(ValueError, "maximum candidates"):
                DiscoveryService(FakeProvider(), repository).discover(
                    "retirement", maximum_candidates=0,
                )

    def test_provider_results_past_per_query_batch_are_not_inspected(self) -> None:
        primary = [Channel(f"UC{i:03}", f"Candidate {i}") for i in range(101)]
        secondary = Channel("UC-secondary", "Secondary candidate")
        batches = {
            "retirement": primary,
            "retirement income": [secondary],
        }
        matches = {channel.channel_id: 0 for channel in primary}
        matches[secondary.channel_id] = 20
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = self._profile(repository, "retirement income")
            provider = self._multi_query_provider(batches, matches)

            report = DiscoveryService(provider, repository).discover(
                "retirement", 1, topic_profile_id=profile.id, maximum_candidates=200,
            )

            self.assertEqual(provider.search_calls, [("retirement", 100), ("retirement income", 100)])
            self.assertNotIn("UC100", provider.verify_calls)
            self.assertEqual(provider.verify_calls[-1], "UC-secondary")
            self.assertEqual(report.candidate_count, 101)

    def test_secondary_search_failure_is_recorded_and_later_queries_continue(self) -> None:
        class Provider:
            def __init__(self):
                self.search_calls = []

            def search(self, keyword, limit):
                self.search_calls.append(keyword)
                if keyword == "broken query":
                    raise RuntimeError("search unavailable")
                channels = [Channel("UC-later", "Later candidate")] if keyword == "later query" else []
                return DiscoveryBatch(len(channels), channels, "fake")

            def verify(self, channel, sample_size=20):
                return ChannelVerification(channel, ["Retirement planning advice"] * sample_size, _dated_videos(channel, sample_size))

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = self._profile(repository, "broken query", "later query")
            provider = Provider()

            report = DiscoveryService(provider, repository).discover(
                "retirement", 1, topic_profile_id=profile.id,
            )

            self.assertEqual(provider.search_calls, ["retirement", "broken query", "later query"])
            self.assertEqual(report.executed_queries, ["retirement", "broken query", "later query"])
            self.assertEqual(
                [(metric.query, metric.raw_results, metric.failure) for metric in report.query_metrics],
                [
                    ("retirement", 0, None),
                    ("broken query", 0, "search unavailable"),
                    ("later query", 1, None),
                ],
            )
            self.assertTrue(repository.discovery_exists("UC-later", "retirement", "fake"))
            self.assertFalse(repository.discovery_exists("UC-later", "later query", "fake"))
            snapshot = repository.get_discovery_relevance_run(report.audit_run_id)
            self.assertEqual(snapshot["query_metrics"][1]["failure"], "search unavailable")

    def test_primary_search_failure_still_aborts_discovery(self) -> None:
        class Provider:
            def __init__(self):
                self.search_calls = []

            def search(self, keyword, limit):
                self.search_calls.append(keyword)
                raise RuntimeError("primary unavailable")

            def verify(self, channel, sample_size=20):
                raise AssertionError("primary failure must not verify")

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = self._profile(repository, "later query")
            provider = Provider()

            with self.assertRaisesRegex(RuntimeError, "primary unavailable"):
                DiscoveryService(provider, repository).discover(
                    "retirement", 1, topic_profile_id=profile.id,
                )

            self.assertEqual(provider.search_calls, ["retirement"])

    def test_audit_snapshots_profile_matching_and_query_plan(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Living Alone")], "fake")

            def verify(self, channel, sample_size=20):
                return ChannelVerification(channel, ["Why I enjoy living alone"] * sample_size, _dated_videos(channel, sample_size))

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            profile = repository.create_topic_profile(
                "Solo Aging", "", ["living alone"], ["aging in place"],
            )
            report = DiscoveryService(Provider(), repository).discover(
                "solo aging", 1, topic_profile_id=profile.id,
            )

            repository.update_topic_profile(
                profile.id, "Changed Profile", "", ["independent retirement"], ["new search"],
            )
            snapshot = repository.get_discovery_relevance_run(report.audit_run_id)

            self.assertEqual(snapshot["profile_name"], "Solo Aging")
            self.assertIn("living alone", snapshot["effective_concepts"])
            self.assertNotIn("independent retirement", snapshot["effective_concepts"])
            self.assertEqual(snapshot["planned_queries"], ["solo aging", "aging in place"])
            self.assertEqual(snapshot["executed_queries"], ["solo aging", "aging in place"])
            self.assertEqual(snapshot["query_metrics"][0]["query"], "solo aging")
            self.assertEqual(snapshot["query_metrics"][0]["topic_accepted_found"], 1)
            self.assertEqual(snapshot["query_metrics"][0]["final_qualified_found"], 1)

    def test_normalizes_stable_channel_id(self) -> None:
        channel = normalize_channel(
            {
                "channel_id": "UC123",
                "channel": "Example Channel",
                "channel_url": "/channel/UC123/",
                "channel_follower_count": 42,
            }
        )
        self.assertIsNotNone(channel)
        self.assertEqual(channel.channel_id, "UC123")
        self.assertEqual(channel.channel_url, "https://www.youtube.com/channel/UC123")
        self.assertEqual(channel.subscriber_count, 42)

    def test_skips_ambiguous_uploader_handle(self) -> None:
        self.assertIsNone(
            normalize_channel({"uploader_id": "@example", "uploader": "Example"})
        )

    def test_verification_uses_videos_tab_not_channel_tabs(self) -> None:
        self.assertEqual(
            channel_videos_url(Channel("UC123", "Example", channel_url="https://www.youtube.com/channel/UC123")),
            "https://www.youtube.com/channel/UC123/videos",
        )

    def test_existing_channel_with_new_keyword_adds_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = VideoRepository(Path(directory) / "test.db")
            service = DiscoveryService(FakeProvider(), repository)
            first = service.discover("retirement", related_terms=["planning"])
            second = service.discover("social security", related_terms=["planning"])

            self.assertEqual(first.duplicate_results_in_search, 1)
            self.assertEqual(first.new_channels, 1)
            self.assertEqual(second.existing_channels, 1)
            self.assertEqual(second.new_discovery_relationships, 1)
            self.assertEqual(repository.count_channels(), 1)
            self.assertEqual(repository.count_discovery_relationships(), 2)

    def test_discovery_auto_scores_new_and_new_provenance_only(self) -> None:
        class Lifecycle:
            def __init__(self, repository):
                self.calls = []
                self.repository = repository

            def score_channels(self, channel_ids):
                self.calls.append(set(channel_ids))
                for channel_id in channel_ids:
                    ChannelScoringService(self.repository).score_channel(channel_id)
                return type("Result", (), {"channels_scored": len(channel_ids), "scoring_failures": []})()

        with tempfile.TemporaryDirectory() as directory:
            repository = VideoRepository(Path(directory) / "test.db")
            lifecycle = Lifecycle(repository)
            service = DiscoveryService(FakeProvider(), repository, lifecycle)
            first = service.discover("retirement", related_terms=["planning"])
            second = service.discover("retirement", related_terms=["planning"])
            third = service.discover("social security", related_terms=["planning"])
            self.assertEqual(first.channels_scored, 1)
            self.assertEqual(second.channels_scored, 0)
            self.assertEqual(third.channels_scored, 1)
            self.assertEqual(lifecycle.calls, [{"UC123"}, set(), {"UC123"}])

    def test_discovery_dry_run_does_not_score(self) -> None:
        class Lifecycle:
            def score_channels(self, channel_ids):
                raise AssertionError("dry run scored")

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            report = DiscoveryService(FakeProvider(), repository, Lifecycle()).discover("retirement", related_terms=["planning"], dry_run=True)
            self.assertEqual(report.channels_scored, 0)
            self.assertEqual(repository.count_channels(), 0)

    def test_scoring_failure_does_not_fail_discovery(self) -> None:
        class Lifecycle:
            def score_channels(self, channel_ids):
                return type("Result", (), {"channels_scored": 0, "scoring_failures": [("UC123", "score failed")]})()

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            report = DiscoveryService(FakeProvider(), repository, Lifecycle()).discover("retirement", related_terms=["planning"])
            self.assertEqual(report.new_channels, 1)
            self.assertEqual(report.scoring_failures, [("UC123", "score failed")])

    def test_isolated_search_hit_is_rejected_and_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            provider = self._provider([Channel("UC1", "Candidate")], matches=1)
            report = DiscoveryService(provider, repository).discover("retirement", 1, related_terms=["planning"])
            self.assertEqual(report.accepted_count, 0)
            self.assertEqual(report.rejected_candidates[0].evidence.reason, "Coverage below Balanced minimum.")
            self.assertIsNone(repository.get_channel("UC1"))

    def test_existing_rejected_channel_gets_no_new_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            repository.upsert_channel(Channel("UC1", "Existing"))
            provider = self._provider([Channel("UC1", "Existing")], matches=2)
            report = DiscoveryService(provider, repository).discover("retirement", 1, related_terms=["planning"])
            self.assertEqual(report.rejected_count, 1)
            self.assertEqual(repository.count_discovery_relationships(), 0)

    def test_accepted_limit_continues_past_rejected_candidates(self) -> None:
        channels = [Channel(f"UC{i}", f"Candidate {i}") for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            provider = self._provider(channels, matches={"UC1": 1, "UC2": 1, "UC3": 20, "UC4": 20})
            report = DiscoveryService(provider, repository).discover("retirement", 2, related_terms=["planning"])
            self.assertEqual(report.accepted_count, 2)
            self.assertEqual(provider.verify_calls, ["UC1", "UC2", "UC3", "UC4"])

    def test_duplicate_candidates_are_verified_once(self) -> None:
        channel = Channel("UC1", "Candidate")
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            provider = self._provider([channel, channel, channel], matches=20)
            report = DiscoveryService(provider, repository).discover("retirement", 1, related_terms=["planning"])
            self.assertEqual(report.unique_channels_in_search, 1)
            self.assertEqual(provider.verify_calls, ["UC1"])

    def test_verification_failure_is_rejected_and_other_candidates_continue(self) -> None:
        channels = [Channel("UC1", "Broken"), Channel("UC2", "Valid")]
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            provider = self._provider(channels, matches=20, fail_ids={"UC1"})
            report = DiscoveryService(provider, repository).discover("retirement", 1, related_terms=["planning"])
            self.assertEqual(report.accepted_count, 1)
            self.assertIn("verification_failed", report.rejected_candidates[0].evidence.reason)
            self.assertIsNotNone(repository.get_channel("UC2"))

    def test_rejection_summary_buckets_empty_and_coverage(self) -> None:
        channels = [Channel(f"UC{i}", f"Candidate {i}") for i in range(1, 7)]
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            provider = self._provider(channels, matches={"UC1": 0, "UC2": 1, "UC3": 4, "UC4": 6, "UC5": 8, "UC6": 11})
            report = DiscoveryService(provider, repository).discover("retirement", 20, mode="strict")
            summary = report.rejection_summary()
            self.assertEqual(summary["coverage_0_15"], 2)
            self.assertEqual(summary["coverage_15_25"], 1)
            self.assertEqual(summary["coverage_25_40"], 1)
            self.assertEqual(summary["coverage_40_60"], 0)
            self.assertEqual(summary["no_usable_sample"], 0)


if __name__ == "__main__":
    unittest.main()
