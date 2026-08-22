from __future__ import annotations

import unittest

from src.crawl_yt.database.models import Channel
from src.crawl_yt.discovery.relevance import (
    DISCOVERY_TOPIC_SAMPLE_SIZE,
    evaluate_channel_topic,
)


def titles(matching: int, total: int = 20) -> list[str]:
    return ["Solo retirement planning" if i < matching else "Garden tools review" for i in range(total)]


class DiscoveryRelevanceTests(unittest.TestCase):
    def test_balanced_boundaries(self) -> None:
        self.assertTrue(evaluate_channel_topic(Channel("UC1", "Gardening"), "retirement", [], titles(8), "balanced").accepted)
        self.assertFalse(evaluate_channel_topic(Channel("UC2", "Gardening"), "retirement", [], titles(7), "balanced").accepted)
        self.assertEqual(DISCOVERY_TOPIC_SAMPLE_SIZE, 20)

    def test_mode_boundaries(self) -> None:
        self.assertTrue(evaluate_channel_topic(Channel("UC1", "Gardening"), "retirement", [], titles(12), "strict").accepted)
        self.assertFalse(evaluate_channel_topic(Channel("UC2", "Gardening"), "retirement", [], titles(11), "strict").accepted)
        self.assertTrue(evaluate_channel_topic(Channel("UC3", "Gardening"), "retirement", [], titles(5), "broad").accepted)
        self.assertFalse(evaluate_channel_topic(Channel("UC4", "Gardening"), "retirement", [], titles(4), "broad").accepted)

    def test_identity_exception_requires_floor(self) -> None:
        channel = Channel("UC1", "Solo Aging Life", description="Advice for seniors aging alone and planning retirement independently")
        relevant_titles = ["Solo aging planning"] * 5 + ["Garden tools review"] * 15
        low_titles = ["Solo aging planning"] + ["Garden tools review"] * 19
        self.assertTrue(evaluate_channel_topic(channel, "solo aging", [], relevant_titles, "balanced").accepted)
        self.assertFalse(evaluate_channel_topic(channel, "solo aging", [], low_titles, "balanced").accepted)

    def test_related_terms_and_generic_token_protection(self) -> None:
        related = ["living alone", "aging alone", "solo retirement"]
        self.assertTrue(evaluate_channel_topic(Channel("UC1", "Life"), "solo aging", related, ["Living alone after 60"] * 14 + ["Money news"] * 6, "balanced").accepted)
        generic = evaluate_channel_topic(Channel("UC2", "Life"), "solo aging", [], ["Aging population statistics"] * 20, "balanced")
        self.assertFalse(generic.accepted)


if __name__ == "__main__":
    unittest.main()
