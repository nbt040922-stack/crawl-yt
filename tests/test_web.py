"""Network-free Phase 3A web adapter tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from src.crawl_yt.collectors.video_metadata import VideoMetadata
from src.crawl_yt.database.models import Channel, Transcript, Video
from src.crawl_yt.database.repository import TranscriptRepository
from src.crawl_yt.discovery.channel_discovery import DiscoveryBatch
from src.crawl_yt.transcripts.provider import TranscriptData
from src.crawl_yt.web.app import create_app


class DiscoveryFake:
    def __init__(self):
        self.calls = []

    def search(self, keyword, limit):
        self.calls.append((keyword, limit))
        return DiscoveryBatch(2, [Channel("UC1", "One", channel_url="https://youtube.com/channel/UC1"), Channel("UC3", "Two", channel_url="https://youtube.com/channel/UC3")], "fake")


class EmptyDiscoveryFake:
    def search(self, keyword, limit):
        return DiscoveryBatch(0, [], "fake")


class VideoFake:
    def __init__(self, video=None):
        self.calls = []
        self.video = video

    def iterate_videos(self, channel_id, limit=None):
        self.calls.append((channel_id, limit))
        return [self.video] if self.video else []


class MetadataFake:
    def __init__(self):
        self.calls = []

    def fetch(self, video_id, webpage_url=None):
        self.calls.append((video_id, webpage_url))
        return VideoMetadata(video_id, "fake", title="Enriched title", view_count=42)


class TranscriptFake:
    def __init__(self):
        self.calls = []

    def fetch(self, video_id, webpage_url, preferred_languages):
        self.calls.append((video_id, webpage_url, preferred_languages))
        return TranscriptData(video_id, "en", "fake", "Fetched text", [{"start": 1.2, "text": "Fetched text"}])


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = TranscriptRepository(Path(self.temp.name) / "web.db")
        self.repository.upsert_channel(Channel("UC1", "One", subscriber_count=10))
        self.repository.upsert_channel(Channel("UC2", "Two", subscriber_count=20))
        now = datetime.now(timezone.utc)
        self.repository.record_discovery("UC1", "retirement", "seed", now.replace(hour=8))
        self.repository.record_discovery("UC1", " social security ", "seed", now.replace(hour=9))
        self.repository.record_discovery("UC2", "RETIREMENT", "seed", now.replace(hour=10))
        self.repository.upsert_video(Video("v1", "UC1", "Video one", now))
        self.repository.upsert_video(Video("v2", "UC1", "Video two", now))
        self.repository.upsert_transcript(Transcript("v1", "en", "youtube_auto", "Police arrived.", [{"start": 12.4, "text": "Police arrived."}], now, now))
        self.client = TestClient(create_app(repository=self.repository))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_health_and_dashboard(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Channels", response.text)
        self.assertIn("Videos", response.text)

    def test_pages_and_pagination(self) -> None:
        self.assertEqual(self.client.get("/channels?page=1&per_page=1").status_code, 200)
        self.assertIn("One", self.client.get("/channels").text)
        self.assertEqual(self.client.get("/videos?page=1&per_page=1").status_code, 200)
        self.assertIn("Video one", self.client.get("/videos").text)
        self.assertEqual(self.client.get("/work-plans").status_code, 200)

    def test_detail_and_unknown_404(self) -> None:
        self.assertEqual(self.client.get("/channels/UC1").status_code, 200)
        self.assertEqual(self.client.get("/videos/v1").status_code, 200)
        self.assertEqual(self.client.get("/channels/missing").status_code, 404)
        self.assertEqual(self.client.get("/videos/missing").status_code, 404)

    def test_get_requests_do_not_create_work_plan(self) -> None:
        before = len(self.repository.list_work_plans(100))
        self.client.get("/work-plans")
        self.assertEqual(len(self.repository.list_work_plans(100)), before)

    def test_create_work_plan_post_and_validation(self) -> None:
        response = self.client.post(
            "/work-plans", data={"max_crawls": "0", "max_enrichments": "2", "max_transcripts": "1"}, follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("/work-plans/", response.headers["location"])
        invalid = self.client.post(
            "/work-plans", data={"max_crawls": "-1", "max_enrichments": "2", "max_transcripts": "1"}
        )
        self.assertEqual(invalid.status_code, 400)

    def test_execute_requires_explicit_max_items(self) -> None:
        plan = self.repository.list_work_plans(1)
        if not plan:
            self.repository.create_work_plan(
                __import__("src.crawl_yt.database.models", fromlist=["WorkPlan"]).WorkPlan(
                    None, __import__("datetime").datetime.now(__import__("datetime").timezone.utc), "planned",
                    __import__("src.crawl_yt.database.models", fromlist=["OperationalBudget"]).OperationalBudget(0, 0, 0),
                    {"crawl_channel": 0, "enrich_video": 0, "transcript_video": 0},
                ), []
            )
            plan = self.repository.list_work_plans(1)
        response = self.client.post(f"/work-plans/{plan[0].id}/execute", data={})
        self.assertEqual(response.status_code, 400)

    def test_full_crawl_requires_confirmation(self) -> None:
        response = self.client.post("/channels/UC1/crawl-full", data={})
        self.assertEqual(response.status_code, 400)

    def test_transcript_detail_renders_timestamped_segments(self) -> None:
        response = self.client.get("/videos/v1/transcripts/en/youtube_auto")
        self.assertEqual(response.status_code, 200)
        self.assertIn("00:00:12.400", response.text)
        self.assertIn("Police arrived.", response.text)
        self.assertIn("youtube_auto", response.text)

    def test_missing_transcript_detail_is_404(self) -> None:
        self.assertEqual(self.client.get("/videos/v1/transcripts/fr/youtube_auto").status_code, 404)
        self.assertEqual(self.client.get("/videos/missing/transcripts/en/youtube_auto").status_code, 404)

    def test_dashboard_uses_count_methods_not_due_materialization(self) -> None:
        self.repository.list_channels_due_for_crawl = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("materialized due rows"))
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_web_source_does_not_use_private_connect(self) -> None:
        import inspect
        from src.crawl_yt.web import app as web_app
        self.assertNotIn("._connect(", inspect.getsource(web_app))

    def test_discovery_get_has_no_provider_call_and_post_calls_fake(self) -> None:
        provider = DiscoveryFake()
        app_client = TestClient(create_app(repository=self.repository, discovery_provider=provider))
        self.assertEqual(app_client.get("/discovery").status_code, 200)
        self.assertEqual(provider.calls, [])
        self.assertEqual(app_client.post("/discovery", data={"keyword": "retirement", "limit": "1"}).status_code, 200)
        self.assertEqual(provider.calls, [("retirement", 1)])

    def test_discovery_result_is_structured_and_labels_statuses(self) -> None:
        provider = DiscoveryFake()
        response = TestClient(create_app(repository=self.repository, discovery_provider=provider)).post("/discovery", data={"keyword": "retirement", "limit": "20"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("DiscoveryReport(", response.text)
        for label in ("Search results", "Unique channels", "Duplicate results in search", "New channels", "Existing channels", "New discovery relationships", "Existing discovery relations"):
            self.assertIn(label, response.text)
        self.assertIn("Two", response.text)
        self.assertIn("/channels/UC3", response.text)
        self.assertIn("https://youtube.com/channel/UC3", response.text)
        self.assertIn("target=\"_blank\"", response.text)
        self.assertIn("New", response.text)
        self.assertIn("Existing", response.text)

    def test_empty_discovery_result_is_friendly(self) -> None:
        response = TestClient(create_app(repository=self.repository, discovery_provider=EmptyDiscoveryFake())).post("/discovery", data={"keyword": "unknown", "limit": "20"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("No channels found for this keyword.", response.text)
        self.assertNotIn("DiscoveryReport(", response.text)

    def test_discovery_dry_run_notice_and_discovered_status(self) -> None:
        response = TestClient(create_app(repository=self.repository, discovery_provider=DiscoveryFake())).post("/discovery", data={"keyword": "retirement", "limit": "20", "dry_run": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Dry run — nothing was written to the database.", response.text)
        self.assertIn("Discovered", response.text)

    def test_discovery_history_shows_keyword_counts_and_links(self) -> None:
        response = self.client.get("/discovery")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Discovery History", response.text)
        self.assertIn("retirement", response.text)
        self.assertIn("/discovery/keywords/retirement", response.text)
        self.assertIn(">2<", response.text)

    def test_keyword_detail_is_paginated_and_associated_only(self) -> None:
        response = self.client.get("/discovery/keywords/retirement?page=1&per_page=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Discovery: retirement", response.text)
        self.assertIn("Total channels", response.text)
        self.assertIn("One", response.text)
        self.assertIn("Page 1", response.text)
        self.assertIn("2 total", response.text)

    def test_keyword_detail_shows_other_keywords_and_unicode(self) -> None:
        response = self.client.get("/discovery/keywords/social%20security")
        self.assertEqual(response.status_code, 200)
        self.assertIn("One", response.text)
        self.assertIn("retirement", response.text)

    def test_channels_keyword_filter_and_provenance(self) -> None:
        response = self.client.get("/channels?keyword=retirement")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Discovery keywords", response.text)
        self.assertIn("One", response.text)
        self.assertIn("Two", response.text)
        self.assertIn("retirement", response.text)

    def test_channel_detail_links_provenance(self) -> None:
        response = self.client.get("/channels/UC1")
        self.assertIn("/discovery/keywords/retirement", response.text)
        self.assertIn("/discovery/keywords/social%20security", response.text)

    def test_empty_discovery_history_and_keyword_states(self) -> None:
        empty_repo = TranscriptRepository(Path(self.temp.name) / "empty.db")
        empty_repo.upsert_channel(Channel("UC9", "No provenance"))
        empty_client = TestClient(create_app(repository=empty_repo))
        self.assertIn("No discovery searches have been saved yet.", empty_client.get("/discovery").text)
        self.assertIn("No channels are currently associated with this keyword.", empty_client.get("/discovery/keywords/unknown").text)

    def test_channel_crawl_post_calls_fake_provider(self) -> None:
        provider = VideoFake(Video("v3", "UC1", "New video", datetime.now(timezone.utc)))
        app_client = TestClient(create_app(repository=self.repository, video_provider=provider))
        response = app_client.post("/channels/UC1/crawl", data={}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(provider.calls, [("UC1", None)])

    def test_video_actions_call_fake_services(self) -> None:
        metadata = MetadataFake()
        transcript = TranscriptFake()
        app_client = TestClient(create_app(repository=self.repository, metadata_provider=metadata, transcript_provider=transcript))
        self.assertEqual(app_client.post("/videos/v1/score", follow_redirects=False).status_code, 303)
        self.assertEqual(app_client.post("/videos/v2/enrich", follow_redirects=False).status_code, 303)
        self.assertEqual(app_client.post("/videos/v2/transcript", data={"language": "en"}, follow_redirects=False).status_code, 303)
        self.assertEqual(metadata.calls[0][0], "v2")
        self.assertEqual(transcript.calls[0][2], ("en",))

    def test_unknown_action_targets_are_404(self) -> None:
        self.assertEqual(self.client.post("/channels/missing/crawl", data={}).status_code, 404)
        self.assertEqual(self.client.post("/videos/missing/score").status_code, 404)
        self.assertEqual(self.client.post("/videos/missing/enrich").status_code, 404)
        self.assertEqual(self.client.post("/videos/missing/transcript", data={}).status_code, 404)
        self.assertEqual(self.client.get("/work-plans/999999").status_code, 404)
        self.assertEqual(self.client.post("/work-plans/999999/execute", data={"max_items": "1"}).status_code, 404)

    def test_provider_failure_is_friendly_html(self) -> None:
        class FailingVideoProvider:
            def iterate_videos(self, channel_id, limit=None):
                raise RuntimeError("provider unavailable")
        response = TestClient(create_app(repository=self.repository, video_provider=FailingVideoProvider()), raise_server_exceptions=False).post("/channels/UC1/crawl", data={})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Operation failed", response.text)

    def test_transcript_action_is_caption_only(self) -> None:
        transcript = TranscriptFake()
        self.assertEqual(TestClient(create_app(repository=self.repository, transcript_provider=transcript)).post("/videos/v2/transcript", data={}, follow_redirects=False).status_code, 303)
        self.assertEqual(transcript.calls[0][2], ("en", "en-US", "en-GB"))

    def test_work_plan_execution_uses_mocked_provider(self) -> None:
        metadata = MetadataFake()
        app_client = TestClient(create_app(repository=self.repository, metadata_provider=metadata))
        created = app_client.post("/work-plans", data={"max_crawls": "0", "max_enrichments": "1", "max_transcripts": "0"}, follow_redirects=False)
        self.assertEqual(created.status_code, 303)
        plan_id = created.headers["location"].rsplit("/", 1)[-1]
        executed = app_client.post(f"/work-plans/{plan_id}/execute", data={"max_items": "1"}, follow_redirects=False)
        self.assertEqual(executed.status_code, 303)
        self.assertTrue(metadata.calls)


if __name__ == "__main__":
    unittest.main()
