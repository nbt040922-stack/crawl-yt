"""Network-free discovery normalization tests."""

from __future__ import annotations

import unittest

from src.crawl_yt.discovery.ytdlp_provider import normalize_channel


class DiscoveryNormalizationTests(unittest.TestCase):
    def test_normalizes_channel_result(self) -> None:
        channel = normalize_channel(
            {
                "channel_id": "UC123",
                "channel": "Example Channel",
                "channel_url": "/channel/UC123/",
                "channel_follower_count": 42,
            },
            "retirement",
        )
        self.assertIsNotNone(channel)
        self.assertEqual(channel.channel_id, "UC123")
        self.assertEqual(channel.channel_url, "https://www.youtube.com/channel/UC123")
        self.assertEqual(channel.subscriber_count, 42)
        self.assertEqual(channel.discovery_source, "yt-dlp:ytsearch")

    def test_skips_result_without_channel_identity(self) -> None:
        self.assertIsNone(normalize_channel({"title": "Incomplete"}, "retirement"))


if __name__ == "__main__":
    unittest.main()
