from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.cadence import (
    CadenceProbe,
    CadenceStatus,
    cadence_band,
    evidence_from_probe,
    evaluate_cadence,
    rates_from_dates,
)
from src.crawl_yt.discovery.channel_discovery import (
    ChannelVerification,
    DiscoveryBatch,
    DiscoveryService,
)
from src.crawl_yt.discovery.ytdlp_provider import YtDlpDiscoveryProvider


class DiscoveryCadenceTests(unittest.TestCase):
    @staticmethod
    def _qualified_videos(channel: Channel, count: int = 20) -> list[Video]:
        count = min(count, 19)
        now = datetime.now(timezone.utc)
        return [
            Video(f"v{index}", channel.channel_id, "retirement planning", now, published_at=now - timedelta(days=index % 20))
            for index in range(count)
        ]

    def test_cadence_bands_and_boundary(self) -> None:
        self.assertEqual(cadence_band(2.99), "below target")
        self.assertEqual(cadence_band(3.0), "good")
        self.assertEqual(cadence_band(5.0), "very good")
        self.assertEqual(cadence_band(7.0), "excellent")

        self.assertEqual(evaluate_cadence(3.0).status, CadenceStatus.QUALIFIED)
        self.assertEqual(evaluate_cadence(2.99).status, CadenceStatus.BELOW_TARGET)

    def test_probe_prevents_topic_sample_from_capping_high_frequency_channel(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        topic_sample = [now - timedelta(days=index) for index in range(20)]
        probe_dates = [now - timedelta(days=index * 7 / 7) for index in range(30)]
        result = DiscoveryService._cadence_from_probe(
            CadenceProbe(tuple(probe_dates), exhausted=False), now
        )
        self.assertGreaterEqual(result.videos_per_week or 0, 7.0)
        self.assertNotAlmostEqual(
            len(topic_sample) / (30 / 7), result.videos_per_week or 0
        )

    def test_incomplete_probe_is_insufficient_instead_of_trusted_low_rate(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        dates = tuple(now - timedelta(days=index) for index in range(20))
        result = DiscoveryService._cadence_from_probe(
            CadenceProbe(dates, exhausted=False), now
        )
        self.assertEqual(result.status, CadenceStatus.INSUFFICIENT_DATA)

    def test_probe_classifies_three_five_and_below_three(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        cases = ((22, "very good", CadenceStatus.QUALIFIED), (13, "good", CadenceStatus.QUALIFIED), (12, "below target", CadenceStatus.BELOW_TARGET))
        for count, band, status in cases:
            dates = tuple(now - timedelta(days=index) for index in range(count))
            evidence = evidence_from_probe(CadenceProbe(dates, exhausted=True), now)
            self.assertEqual(evidence.band, band)
            self.assertEqual(evidence.status, status)

    def test_flat_dates_need_no_individual_enrichment(self) -> None:
        entries = [{"id": f"v{index}", "upload_date": "20260801"} for index in range(3)]
        fake = _FakeYoutubeDL({"entries": entries})
        with patch("src.crawl_yt.discovery.ytdlp_provider.YoutubeDL", fake.factory):
            probe = YtDlpDiscoveryProvider().probe_cadence(Channel("UC1", "One"))
        self.assertEqual(fake.video_calls, [])
        self.assertEqual(probe.dates_available, 3)
        self.assertEqual(probe.dates_enriched, 0)

    def test_missing_flat_dates_are_enriched_with_bounded_metadata_calls(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        entries = [{"id": f"v{index}"} for index in range(13)]
        metadata = {f"v{index}": {"id": f"v{index}", "timestamp": (now - timedelta(days=index * 2)).timestamp()} for index in range(13)}
        fake = _FakeYoutubeDL({"entries": entries}, metadata)
        with patch("src.crawl_yt.discovery.ytdlp_provider.YoutubeDL", fake.factory):
            probe = YtDlpDiscoveryProvider().probe_cadence(Channel("UC1", "One"))
        evidence = evidence_from_probe(probe, now)
        self.assertEqual(evidence.status, CadenceStatus.QUALIFIED)
        self.assertEqual(probe.dates_enriched, 13)
        self.assertLessEqual(len(fake.video_calls), 40)

    def test_duplicate_ids_are_enriched_once_and_one_failure_does_not_abort(self) -> None:
        entries = [{"id": "v1"}, {"id": "v1"}, {"id": "v2"}]
        fake = _FakeYoutubeDL({"entries": entries}, {"v1": {"id": "v1", "upload_date": "20260801"}, "v2": RuntimeError("metadata failed")})
        with patch("src.crawl_yt.discovery.ytdlp_provider.YoutubeDL", fake.factory):
            probe = YtDlpDiscoveryProvider().probe_cadence(Channel("UC1", "One"))
        self.assertEqual(fake.video_calls.count("v1"), 1)
        self.assertEqual(probe.enrichment_failures, 1)

    def test_date_enrichment_is_bounded_at_forty_calls(self) -> None:
        fake = _FakeYoutubeDL({"entries": [{"id": f"v{index}"} for index in range(100)]}, {})
        with patch("src.crawl_yt.discovery.ytdlp_provider.YoutubeDL", fake.factory):
            probe = YtDlpDiscoveryProvider().probe_cadence(Channel("UC1", "One"))
        self.assertEqual(len(fake.video_calls), 40)
        self.assertEqual(probe.dates_enriched, 40)
        self.assertEqual(probe.dates_available, 0)

    def test_cadence_probe_runs_only_after_topic_pass(self) -> None:
        class Provider:
            def __init__(self):
                self.probed = []

            def search(self, keyword, limit):
                return DiscoveryBatch(2, [Channel("UC-good", "Retirement Planning"), Channel("UC-bad", "Cooking")], "search")

            def verify(self, channel, sample_size=20):
                title = "retirement planning" if channel.channel_id == "UC-good" else "cooking recipes"
                return ChannelVerification(channel, [title] * 20, [])

            def probe_cadence(self, channel, **kwargs):
                self.probed.append(channel.channel_id)
                now = datetime.now(timezone.utc)
                return CadenceProbe(tuple(now - timedelta(days=index * 2) for index in range(13)), exhausted=True)

        with tempfile.TemporaryDirectory() as directory:
            provider = Provider()
            report = DiscoveryService(provider, ChannelRepository(Path(directory) / "db.sqlite")).discover("retirement", 1, related_terms=["planning"])
        self.assertEqual(provider.probed, ["UC-good"])
        self.assertEqual(report.topic_accepted_count, 1)

    def test_missing_cadence_data_is_not_zero(self) -> None:
        result = evaluate_cadence(None)
        self.assertEqual(result.status, CadenceStatus.INSUFFICIENT_DATA)
        self.assertIsNone(result.videos_per_week)
        self.assertEqual(result.reason, "Insufficient cadence data")

    def test_rates_reuse_fixed_30_and_90_day_windows(self) -> None:
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        dates = [now - timedelta(days=offset) for offset in (0, 3, 7, 10, 20, 30, 60)]
        rates = rates_from_dates(dates, now)
        self.assertAlmostEqual(rates.videos_per_week_30d, 6 / (30 / 7))
        self.assertAlmostEqual(rates.videos_per_week_90d, 7 / (90 / 7))

    def test_topic_pass_below_cadence_does_not_persist_new_channel(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Active Life")], "search")

            def verify(self, channel, sample_size=20):
                return ChannelVerification(
                    channel,
                    ["retirement planning", "living alone tips"] * 10,
                    [Video(
                        "v1", channel.channel_id, "retirement planning",
                        datetime.now(timezone.utc),
                        published_at=datetime.now(timezone.utc),
                    )],
                )

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            report = DiscoveryService(Provider(), repository).discover(
                "retirement", 1, related_terms=["living alone"]
            )
            self.assertEqual(report.topic_accepted_count, 1)
            self.assertEqual(report.cadence_qualified_count, 0)
            self.assertEqual(report.cadence_below_target_count, 1)
            self.assertIsNone(repository.get_channel("UC1"))

    def test_qualified_new_channel_is_crawled_before_final_acceptance(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Retirement Planning")], "search")

            def verify(self, channel, sample_size=20):
                videos = DiscoveryCadenceTests._qualified_videos(channel)
                return ChannelVerification(channel, [video.title for video in videos], videos)

        class Crawl:
            def __init__(self):
                self.calls = []

            def crawl(self, channel_id, *, full=False):
                self.calls.append((channel_id, full))

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            crawl = Crawl()
            report = DiscoveryService(Provider(), repository, initial_crawl_service=crawl).discover("retirement", 1)
            self.assertEqual(crawl.calls, [("UC1", True)])
            self.assertEqual(report.final_qualified_count, 1)
            self.assertEqual(report.full_crawled_count, 1)
            self.assertIsNotNone(repository.get_channel("UC1"))


class _FakeYoutubeDL:
    def __init__(self, channel_info, metadata=None):
        self.channel_info = channel_info
        self.metadata = metadata or {}
        self.video_calls = []

    def factory(self, options):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if url.endswith("/videos"):
            return self.channel_info
        video_id = url.split("v=")[-1]
        self.video_calls.append(video_id)
        value = self.metadata.get(video_id)
        if isinstance(value, Exception):
            raise value
        return value

    def test_failed_initial_crawl_is_not_finally_qualified(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Retirement Planning")], "search")

            def verify(self, channel, sample_size=20):
                videos = DiscoveryCadenceTests._qualified_videos(channel)
                return ChannelVerification(channel, [video.title for video in videos], videos)

        class Crawl:
            def crawl(self, channel_id, *, full=False):
                raise RuntimeError("crawl unavailable")

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            report = DiscoveryService(Provider(), repository, initial_crawl_service=Crawl()).discover("retirement", 1)
            self.assertEqual(report.final_qualified_count, 0)
            self.assertEqual(report.final_failed_candidates[0].full_crawl_status, "failed")
            self.assertEqual(report.cadence_failed_count, 1)
            self.assertIsNone(repository.get_channel("UC1"))
            self.assertEqual(repository.count_discovery_relationships(), 0)

    def test_early_stop_counts_final_qualified_not_topic_only(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                channels = [Channel("UC2", "Retirement Planning"), Channel("UC1", "Retirement Planning")]
                return DiscoveryBatch(2, channels, "search")

            def verify(self, channel, sample_size=20):
                if channel.channel_id == "UC1":
                    videos = DiscoveryCadenceTests._qualified_videos(channel)
                else:
                    now = datetime.now(timezone.utc)
                    videos = [Video("v", channel.channel_id, "retirement planning", now, published_at=now)]
                return ChannelVerification(channel, ["retirement planning"] * 20, videos)

        with tempfile.TemporaryDirectory() as directory:
            report = DiscoveryService(Provider(), ChannelRepository(Path(directory) / "db.sqlite")).discover("retirement", 1)
            self.assertEqual(report.final_qualified_count, 1)
            self.assertEqual(report.topic_accepted_count, 2)

    def test_existing_channel_below_cadence_is_preserved_without_new_relationship(self) -> None:
        class Provider:
            def search(self, keyword, limit):
                return DiscoveryBatch(1, [Channel("UC1", "Retirement Planning")], "search")

            def verify(self, channel, sample_size=20):
                now = datetime.now(timezone.utc)
                videos = [Video("v", channel.channel_id, "retirement planning", now, published_at=now)]
                return ChannelVerification(channel, ["retirement planning"] * 20, videos)

        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "db.sqlite")
            repository.upsert_channel(Channel("UC1", "Retirement Planning"))
            report = DiscoveryService(Provider(), repository).discover("retirement", 1)
            self.assertEqual(report.cadence_below_target_count, 1)
            self.assertEqual(report.new_discovery_relationships, 0)
            self.assertIsNotNone(repository.get_channel("UC1"))


if __name__ == "__main__":
    unittest.main()
