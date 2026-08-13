"""CLI parsing and dependency-injected command tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.crawl_yt.cli import build_parser, main
from src.crawl_yt.database.models import Channel
from src.crawl_yt.database.repository import ChannelRepository
from src.crawl_yt.discovery.channel_discovery import DiscoveryBatch


class FakeProvider:
    def search(self, keyword: str, limit: int) -> DiscoveryBatch:
        return DiscoveryBatch(
            search_results=2,
            channels=[Channel("UC1", "One")],
            source="test",
        )


class CliTests(unittest.TestCase):
    def test_discover_argument_parsing(self) -> None:
        args = build_parser().parse_args(
            ["discover", "retirement", "--limit", "100", "--dry-run"]
        )
        self.assertEqual(args.keyword, "retirement")
        self.assertEqual(args.limit, 100)
        self.assertTrue(args.dry_run)

    def test_crawl_argument_parsing(self) -> None:
        args = build_parser().parse_args(
            ["crawl", "UC123", "--limit", "20", "--full", "--known-stop-threshold", "7"]
        )
        self.assertEqual(args.channel, "UC123")
        self.assertEqual(args.limit, 20)
        self.assertTrue(args.full)
        self.assertEqual(args.known_stop_threshold, 7)

        all_args = build_parser().parse_args(
            ["crawl-all", "--max-channels", "3", "--limit-per-channel", "10"]
        )
        self.assertEqual(all_args.max_channels, 3)
        self.assertEqual(all_args.limit_per_channel, 10)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["crawl-due"])
        self.assertEqual(
            build_parser().parse_args(["crawl-due", "--limit", "20"]).limit, 20
        )

    def test_enrichment_argument_parsing_and_required_limits(self) -> None:
        single = build_parser().parse_args(["enrich", "video-1"])
        self.assertEqual(single.video_id, "video-1")
        channel = build_parser().parse_args(
            ["enrich-channel", "UC123", "--limit", "5"]
        )
        self.assertEqual(channel.limit, 5)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["enrich-channel", "UC123"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["enrich-pending"])

    def test_transcript_argument_parsing_and_required_limits(self) -> None:
        single = build_parser().parse_args(
            ["transcript", "video-1", "--lang", "en", "--force"]
        )
        self.assertEqual(single.video_id, "video-1")
        self.assertEqual(single.lang, "en")
        self.assertTrue(single.force)
        channel = build_parser().parse_args(
            ["transcript-channel", "UC123", "--limit", "20", "--lang", "en"]
        )
        self.assertEqual(channel.limit, 20)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["transcript-channel", "UC123"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["transcript-pending"])

    def test_scoring_argument_parsing_and_required_limits(self) -> None:
        self.assertEqual(
            build_parser().parse_args(["score-channel", "UC123"]).channel, "UC123"
        )
        for command in ("score-all", "top-channels"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                build_parser().parse_args([command])
            self.assertEqual(
                build_parser().parse_args([command, "--limit", "20"]).limit, 20
            )

    def test_expansion_limits_are_required(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["expand", "retirement"])
        args = build_parser().parse_args(
            [
                "expand", "retirement", "--max-depth", "2",
                "--channel-budget", "500", "--query-budget", "30",
                "--results-per-query", "20", "--dry-run",
            ]
        )
        self.assertEqual((args.max_depth, args.channel_budget, args.query_budget), (2, 500, 30))
        self.assertTrue(args.dry_run)

    def test_work_plan_budgets_and_execution_limit_are_required(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["plan-work"])
        args = build_parser().parse_args([
            "plan-work", "--max-crawls", "3", "--max-enrichments", "5",
            "--max-transcripts", "5",
        ])
        self.assertEqual((args.max_crawls, args.max_enrichments, args.max_transcripts), (3, 5, 5))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["execute-plan", "1"])

    def test_dry_run_does_not_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ChannelRepository(Path(directory) / "test.db")
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    ["discover", "retirement", "--dry-run"],
                    discovery_provider=FakeProvider(),
                    repository=repository,
                )
            self.assertEqual(result, 0)
            self.assertEqual(repository.count_channels(), 0)
            self.assertEqual(repository.count_discovery_relationships(), 0)
            self.assertIn("Dry run", output.getvalue())


if __name__ == "__main__":
    unittest.main()
