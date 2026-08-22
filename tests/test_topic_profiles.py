from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.channel_discovery import ChannelVerification, DiscoveryBatch, DiscoveryService


def _dated_videos(channel: Channel, count: int = 20) -> list[Video]:
    now = datetime.now(timezone.utc)
    return [Video(f"{channel.channel_id}-{index}", channel.channel_id, "sample", now, published_at=now - timedelta(days=index)) for index in range(count)]


class TopicProfileRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = ChannelRepository(Path(self.temp.name) / "profiles.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_profile_crud_normalizes_and_deduplicates_concepts(self) -> None:
        profile = self.repository.create_topic_profile(
            "Solo Aging", "Independent later life",
            [" Living Alone ", "living   alone", "AGING ALONE", ".", "aging"],
            [" Aging ", "aging", "Independent  Living "],
        )
        self.assertEqual(profile.concept_phrases, ["living alone", "aging alone"])
        self.assertEqual(profile.search_concepts, ["aging", "independent living"])
        self.assertEqual(self.repository.get_topic_profile(profile.id).name, "Solo Aging")
        self.assertEqual([item.id for item in self.repository.list_topic_profiles()], [profile.id])

        updated = self.repository.update_topic_profile(
            profile.id, "Solo Aging Updated", "", ["senior life"],
            ["Retirement Community", "retirement   community"],
        )
        self.assertEqual(
            (updated.name, updated.concept_phrases, updated.search_concepts),
            ("Solo Aging Updated", ["senior life"], ["retirement community"]),
        )
        self.assertTrue(self.repository.delete_topic_profile(profile.id))
        self.assertIsNone(self.repository.get_topic_profile(profile.id))

    def test_profile_requires_name_and_meaningful_concept(self) -> None:
        with self.assertRaisesRegex(ValueError, "name"):
            self.repository.create_topic_profile(" ", "", ["living alone"])
        with self.assertRaisesRegex(ValueError, "concept"):
            self.repository.create_topic_profile("Solo Aging", "", [" "])
        with self.assertRaisesRegex(ValueError, "meaningful concept"):
            self.repository.create_topic_profile("Too broad", "", ["aging", "life"])
        with self.assertRaisesRegex(ValueError, "meaningful concept"):
            self.repository.create_topic_profile("Punctuation", "", [".", "---"])

    def test_profile_update_preserves_omitted_search_concepts(self) -> None:
        profile = self.repository.create_topic_profile(
            "Solo Aging", "", ["living alone"], ["aging in place"],
        )

        preserved = self.repository.update_topic_profile(
            profile.id, "Solo Aging", "Updated", ["independent living"],
        )
        self.assertEqual(preserved.search_concepts, ["aging in place"])

        cleared = self.repository.update_topic_profile(
            profile.id, "Solo Aging", "Updated", ["independent living"], [],
        )
        self.assertEqual(cleared.search_concepts, [])

    def test_discovery_snapshots_profile_concepts_and_blocks_delete_after_use(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Senior Solo")], "fake")

            def verify(self, channel, sample_size=20):
                return ChannelVerification(channel, ["Why I enjoy living alone"] * sample_size, _dated_videos(channel, sample_size))

        profile = self.repository.create_topic_profile("Solo Aging", "", ["living alone"])
        report = DiscoveryService(Provider(), self.repository).discover(
            "solo aging", 1, topic_profile_id=profile.id
        )
        self.assertEqual(report.topic_profile_name, "Solo Aging")
        self.assertIn("living alone", report.effective_concepts)
        self.assertIsNotNone(report.audit_run_id)

        self.repository.update_topic_profile(profile.id, "Solo Aging", "", ["independent retirement"])
        snapshot = self.repository.get_discovery_relevance_run(report.audit_run_id)
        self.assertIn("living alone", snapshot["effective_concepts"])
        self.assertNotIn("independent retirement", snapshot["effective_concepts"])
        evidence = snapshot["candidate_evidence"][0]
        self.assertEqual(evidence["matched_concepts"], ["living alone"])
        self.assertEqual(evidence["distinct_matched_concepts"], 1)
        self.assertEqual(evidence["title_evidence"][0], {
            "title": "Why I enjoy living alone",
            "matched_concepts": ["living alone"],
        })
        self.assertFalse(self.repository.delete_topic_profile(profile.id))

    def test_profile_deleted_during_verification_keeps_audit_snapshot(self) -> None:
        profile = self.repository.create_topic_profile("Solo Aging", "", ["living alone"])

        class Provider:
            def search(inner_self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC-RACE", "Senior Solo")], "fake")

            def verify(inner_self, channel, sample_size=20):
                self.assertTrue(self.repository.delete_topic_profile(profile.id))
                return ChannelVerification(channel, ["Why I enjoy living alone"] * sample_size, _dated_videos(channel, sample_size))

        report = DiscoveryService(Provider(), self.repository).discover(
            "solo aging", 1, topic_profile_id=profile.id
        )
        snapshot = self.repository.get_discovery_relevance_run(report.audit_run_id)
        self.assertEqual(snapshot["profile_name"], "Solo Aging")
        self.assertIsNone(snapshot["profile_id"])
        self.assertIn("living alone", snapshot["effective_concepts"])

    def test_profile_changes_matching_without_changing_search_or_provenance(self) -> None:
        class Provider:
            def __init__(self):
                self.search_calls = []
                self.verify_calls = []

            def search(self, keyword, limit):
                self.search_calls.append((keyword, limit))
                return DiscoveryBatch(1, [Channel("UC2", "Senior Path")], "fake")

            def verify(self, channel, sample_size=20):
                self.verify_calls.append((channel.channel_id, sample_size))
                titles = ["Living alone after 65"] * 7 + ["Independent aging at home"] * 6 + ["Garden tour"] * 7
                return ChannelVerification(channel, titles, _dated_videos(channel, sample_size))

        profile = self.repository.create_topic_profile("Solo Aging", "", ["living alone"])
        without_provider = Provider()
        without = DiscoveryService(without_provider, self.repository).discover("solo aging", 1)
        with_provider = Provider()
        with_profile = DiscoveryService(with_provider, self.repository).discover(
            "solo aging", 1, topic_profile_id=profile.id, extra_concepts="independent aging"
        )
        self.assertEqual(without.accepted_count, 0)
        self.assertEqual(with_profile.accepted_count, 1)
        self.assertEqual(without_provider.search_calls, with_provider.search_calls)
        self.assertEqual(without_provider.verify_calls, with_provider.verify_calls)
        self.assertTrue(self.repository.discovery_exists("UC2", "solo aging", "fake"))
        self.assertFalse(self.repository.discovery_exists("UC2", "living alone", "fake"))

    def test_dry_run_does_not_persist_relevance_audit(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC3", "Candidate")], "fake")

            def verify(self, channel, sample_size=20):
                return ChannelVerification(channel, ["Retirement planning"] * sample_size, _dated_videos(channel, sample_size))

        DiscoveryService(Provider(), self.repository).discover("retirement", 1, dry_run=True)
        with self.repository._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM discovery_relevance_runs").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
