from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.activity import (
    CandidateActivitySignal,
    DiscoverySearchResult,
    DiscoverySearchResult,
    activity_hint,
    activity_priority_score,
)
from src.crawl_yt.discovery.channel_discovery import DiscoveryBatch, DiscoveryService
from src.crawl_yt.discovery.channel_discovery import ChannelVerification
from src.crawl_yt.discovery.cadence import CadenceProbe
from src.crawl_yt.database.models import Video


class ActivityTests(unittest.TestCase):
    def test_active_candidate_ranks_ahead_of_stale_candidate(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        active = CandidateActivitySignal()
        active.merge(DiscoverySearchResult(Channel("UCA", "A"), "a1", now - timedelta(days=2)), "q1")
        active.merge(DiscoverySearchResult(Channel("UCA", "A"), "a2", now - timedelta(days=3)), "q2")
        stale = CandidateActivitySignal()
        stale.merge(DiscoverySearchResult(Channel("UCB", "B"), "b1", now - timedelta(days=60)), "q1")
        self.assertGreater(activity_priority_score(active, now), activity_priority_score(stale, now))
        self.assertEqual(activity_hint(active, now), "VERY RECENT")
        self.assertEqual(activity_hint(stale, now), "STALE")

    def test_unknown_activity_survives_with_neutral_priority(self) -> None:
        signal = CandidateActivitySignal()
        self.assertEqual(activity_hint(signal), "UNKNOWN")
        self.assertGreaterEqual(activity_priority_score(signal), 0)

    def test_cross_query_result_merges_video_ids_and_queries(self) -> None:
        signal = CandidateActivitySignal()
        channel = Channel("UC1", "One")
        signal.merge(DiscoverySearchResult(channel, "x", None), "q1")
        signal.merge(DiscoverySearchResult(channel, "y", None), "q2")
        signal.merge(DiscoverySearchResult(channel, "x", None), "q2")
        self.assertEqual(signal.observed_video_ids, {"x", "y"})
        self.assertEqual(signal.query_diversity, 2)
        self.assertEqual(signal.observed_result_count, 3)

    def test_later_query_active_candidate_is_verified_first_and_target_stops(self) -> None:
        now = datetime.now(timezone.utc)
        low = Channel("UCL", "Retirement Planning")
        active = Channel("UCA", "Retirement Planning")

        class Provider:
            def __init__(self):
                self.verify_calls = []

            def search(self, keyword, limit):
                if keyword == "retirement":
                    return DiscoveryBatch(1, [low], "fake", [DiscoverySearchResult(low, "old", now - timedelta(days=60))])
                return DiscoveryBatch(1, [active], "fake", [DiscoverySearchResult(active, "new", now - timedelta(days=2))])

            def verify(self, channel, sample_size=20):
                self.verify_calls.append(channel.channel_id)
                return ChannelVerification(channel, ["retirement planning"] * 20, [])

            def probe_cadence(self, channel, **kwargs):
                return CadenceProbe(tuple(now - timedelta(days=index * 2) for index in range(13)), exhausted=True)

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            profile = repository.create_topic_profile("Retirement", "", ["planning"], ["active creators"])
            provider = Provider()
            report = DiscoveryService(provider, repository).discover("retirement", 1, topic_profile_id=profile.id)
        self.assertEqual(provider.verify_calls, ["UCA"])
        self.assertEqual(report.final_qualified_count, 1)
        self.assertEqual(report.query_metrics[-1].final_qualified_found, 1)

    def test_no_profile_uses_effective_one_concept_threshold(self) -> None:
        channel = Channel("UCV", "Vlog")

        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [channel], "fake")

            def verify(self, channel, sample_size=20):
                return ChannelVerification(channel, ["daily vlog"] * sample_size, [])

            def probe_cadence(self, channel, **kwargs):
                now = datetime.now(timezone.utc)
                return CadenceProbe(tuple(now - timedelta(days=index * 2) for index in range(13)), exhausted=True)

        with tempfile.TemporaryDirectory() as directory:
            report = DiscoveryService(Provider(), ChannelRepository(Path(directory) / "db.sqlite")).discover("vlog", 1)
        self.assertEqual(report.minimum_distinct_concepts, 1)
        self.assertEqual(report.topic_accepted_count, 1)

    def test_final_yield_attributes_all_queries_but_first_discovery_once(self) -> None:
        now = datetime.now(timezone.utc)
        channel = Channel("UCA", "Retirement Planning")

        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [channel], "fake", [DiscoverySearchResult(channel, keyword, now - timedelta(days=2))])

            def verify(self, channel, sample_size=20):
                return ChannelVerification(channel, ["retirement planning"] * 20, [])

            def probe_cadence(self, channel, **kwargs):
                return CadenceProbe(tuple(now - timedelta(days=index * 2) for index in range(13)), exhausted=True)

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            profile = repository.create_topic_profile("Retirement", "", ["planning"], ["active creators"])
            report = DiscoveryService(Provider(), repository).discover("retirement", 1, topic_profile_id=profile.id)
        first, second = report.query_metrics
        self.assertEqual(first.final_qualified_found, 1)
        self.assertEqual(first.final_qualified_first_discovered, 1)
        self.assertEqual(second.final_qualified_found, 1)
        self.assertEqual(second.final_qualified_first_discovered, 0)


if __name__ == "__main__":
    unittest.main()
