"""CLI parsing and dependency-injected command tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
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
        args = build_parser().parse_args(["crawl", "UC123", "--limit", "20"])
        self.assertEqual(args.channel, "UC123")
        self.assertEqual(args.limit, 20)

        all_args = build_parser().parse_args(
            ["crawl-all", "--max-channels", "3", "--limit-per-channel", "10"]
        )
        self.assertEqual(all_args.max_channels, 3)
        self.assertEqual(all_args.limit_per_channel, 10)

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
