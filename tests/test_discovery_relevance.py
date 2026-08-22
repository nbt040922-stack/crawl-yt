from __future__ import annotations

import unittest

from src.crawl_yt.database.models import Channel
from src.crawl_yt.discovery.relevance import (
    DISCOVERY_TOPIC_SAMPLE_SIZE,
    build_effective_concepts,
    evaluate_channel_topic,
    match_topic_concepts,
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

    def test_generic_single_word_queries_do_not_match_by_themselves(self) -> None:
        for query in ("aging", "life", "health", "money", "reality", "living"):
            result = evaluate_channel_topic(
                Channel("UC1", "Unrelated Channel"), query, [], [f"{query.title()} news today"] * 20, "balanced"
            )
            self.assertEqual(result.topic_matches, 0, query)
            self.assertFalse(result.accepted, query)

    def test_explicit_related_phrase_and_multiword_query_still_match(self) -> None:
        primary = evaluate_channel_topic(
            Channel("UC1", "Unrelated"), "solo aging", [], ["The Reality of Solo Aging"] * 20, "balanced"
        )
        related = evaluate_channel_topic(
            Channel("UC2", "Unrelated"), "aging", ["aging alone"], ["The Reality of Aging Alone"] * 20, "balanced"
        )
        self.assertTrue(primary.accepted)
        self.assertTrue(related.accepted)

    def test_generic_single_word_identity_does_not_become_strong(self) -> None:
        result = evaluate_channel_topic(
            Channel("UC1", "Health News", description="Health news and updates"),
            "health", [], ["Unrelated gardening"] * 20, "balanced"
        )
        self.assertNotEqual(result.identity, "strong")
        self.assertFalse(result.accepted)

    def test_effective_concepts_normalize_deduplicate_and_match_with_evidence(self) -> None:
        concepts = build_effective_concepts(
            "Solo Aging", [" Living Alone ", "living   alone", "Senior Life"], ["AGING ALONE"]
        )
        self.assertEqual(concepts, ["solo aging", "living alone", "senior life", "aging alone"])
        self.assertEqual(
            build_effective_concepts("aging", ["living alone"], ["health", "."]),
            ["living alone"],
        )
        self.assertEqual(match_topic_concepts("Why I Chose Living Alone After 65", concepts), ["living alone"])
        self.assertEqual(match_topic_concepts("Why I Live Alone at 67", ["living alone"]), ["living alone"])
        self.assertEqual(
            match_topic_concepts("Living alone as an older adult", ["older adult living alone"]),
            ["older adult living alone"],
        )

    def test_non_word_concepts_and_news_plural_do_not_match(self) -> None:
        self.assertEqual(match_topic_concepts("Update. More later.", ["."]), [])
        self.assertEqual(match_topic_concepts("Life News Today", ["new life"]), [])
        self.assertEqual(match_topic_concepts("A party for everyone", ["art"]), [])
        self.assertEqual(match_topic_concepts("Senior lifestyle ideas", ["senior life"]), [])

    def test_reordered_matching_requires_local_phrase_structure(self) -> None:
        concepts = ["living alone", "life after 60"]
        self.assertEqual(
            match_topic_concepts("Stop living with fear when you are not alone", concepts),
            [],
        )
        self.assertEqual(
            match_topic_concepts("60 recipes after a life change", concepts),
            [],
        )
        self.assertEqual(
            match_topic_concepts(
                "Living alone as an older adult", ["older adult living alone"]
            ),
            ["older adult living alone"],
        )

    def test_top_matched_concepts_are_ranked_by_title_frequency(self) -> None:
        result = evaluate_channel_topic(
            Channel("UC1", "Unrelated"),
            "solo aging",
            ["living alone", "aging alone"],
            ["Living alone today", "Aging alone safely", "Aging alone well", "Aging alone at home"],
            "broad",
        )
        self.assertEqual(result.matched_concepts[:2], ["aging alone", "living alone"])

    def test_profile_concepts_raise_realistic_channel_coverage_with_title_evidence(self) -> None:
        concepts = ["living alone", "aging alone", "life after 60", "life over 60", "senior life", "solo retirement", "independent aging"]
        matching = [
            "Why I Chose Living Alone After 65", "Aging Alone Without Fear", "My Life After 60",
            "How Life Over 60 Really Feels", "Senior Life on My Own", "Planning a Solo Retirement",
            "Independent Aging at Home", "Living Alone and Loving It", "Aging Alone With Confidence",
            "Life After 60: A New Chapter", "Senior Life Daily Routine", "Solo Retirement Budget",
            "Independent Aging Decisions",
        ]
        sample = matching + ["Kitchen tools", "Garden tour", "Favorite books", "Travel vlog", "Family recipe", "Morning walk", "Phone review"]
        with_profile = evaluate_channel_topic(Channel("UC1", "Senior Life Solo Path"), "solo aging", concepts, sample, "balanced")
        without_profile = evaluate_channel_topic(Channel("UC1", "Senior Life Solo Path"), "solo aging", [], sample, "balanced")
        self.assertEqual(with_profile.topic_matches, 13)
        self.assertTrue(with_profile.accepted)
        self.assertLess(without_profile.topic_coverage, with_profile.topic_coverage)
        self.assertIn("living alone", with_profile.matched_concepts)
        self.assertEqual(with_profile.title_evidence[0].matched_concepts, ["living alone"])

    def test_matching_identity_phrase_does_not_rescue_unrelated_music_channel(self) -> None:
        sample = ["Classic songs", "Live performance", "Album review", "Guitar lesson", "Concert footage"] * 4
        result = evaluate_channel_topic(Channel("UCM", "Golden Years Music"), "solo aging", ["golden years"], sample, "balanced")
        self.assertEqual(result.identity, "strong")
        self.assertEqual(result.topic_coverage, 0)
        self.assertFalse(result.accepted)

    def test_profile_concepts_do_not_change_mode_thresholds(self) -> None:
        channel = Channel("UC1", "Unrelated")
        sample = lambda count: ["Living alone after 65"] * count + ["Garden tour"] * (20 - count)
        self.assertFalse(evaluate_channel_topic(channel, "solo aging", ["living alone"], sample(7), "balanced").accepted)
        self.assertTrue(evaluate_channel_topic(channel, "solo aging", ["living alone"], sample(9), "balanced").accepted)
        self.assertTrue(evaluate_channel_topic(channel, "solo aging", ["living alone"], sample(13), "strict").accepted)


if __name__ == "__main__":
    unittest.main()
