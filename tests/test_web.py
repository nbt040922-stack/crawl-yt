"""Network-free Phase 3A web adapter tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.crawl_yt.database.models import Channel, Video
from src.crawl_yt.database.repository import VideoRepository
from src.crawl_yt.web.app import create_app


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = VideoRepository(Path(self.temp.name) / "web.db")
        self.repository.upsert_channel(Channel("UC1", "One", subscriber_count=10))
        self.repository.upsert_video(Video("v1", "UC1", "Video one", __import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
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


if __name__ == "__main__":
    unittest.main()
