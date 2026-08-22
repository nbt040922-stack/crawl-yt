from __future__ import annotations

import unittest

from src.crawl_yt.database.models import Channel
from src.crawl_yt.discovery.relevance import (
    DISCOVERY_TOPIC_SAMPLE_SIZE,
    build_effective_concepts,
    evaluate_channel_topic,
    get_topic_policy,
    match_topic_concepts,
)


def titles(matching: int, total: int = 20) -> list[str]:
    return [
        ("Solo retirement planning" if i % 2 == 0 else "Living alone retirement")
        if i < matching else "Garden tools review"
        for i in range(total)
    ]


class DiscoveryRelevanceTests(unittest.TestCase):
    def _diverse_titles(self, matching: int, total: int = 20) -> list[str]:
        matched = ["Retirement planning" if index % 2 == 0 else "Living alone tips" for index in range(matching)]
        return matched + ["Garden tools review"] * (total - matching)

    def test_calibrated_policies_and_diversity_thresholds(self) -> None:
        self.assertEqual(get_topic_policy("strict").coverage_threshold, 0.40)
        self.assertEqual(get_topic_policy("balanced").coverage_threshold, 0.20)
        self.assertEqual(get_topic_policy("broad").coverage_threshold, 0.10)
        for mode, matching, expected in (("strict", 8, True), ("strict", 7, False), ("balanced", 4, True), ("balanced", 3, False), ("broad", 2, True)):
            result = evaluate_channel_topic(Channel("UC1", "Unrelated"), "retirement", ["living alone"], self._diverse_titles(matching), mode)
            self.assertEqual(result.accepted, expected, (mode, matching))
            self.assertEqual(result.distinct_matched_concepts, 2 if matching else 0)

    def test_balanced_rejects_single_concept_at_new_boundary(self) -> None:
        result = evaluate_channel_topic(
            Channel("UC1", "Unrelated"), "retirement", ["senior life"],
            ["Senior Life"] * 4 + ["Garden tools review"] * 16, "balanced",
        )
        self.assertEqual((result.topic_matches, result.distinct_matched_concepts), (4, 1))
        self.assertFalse(result.accepted)
        self.assertIn("1 distinct concept", result.reason)

    def test_balanced_identity_exception_requires_nonzero_video_evidence(self) -> None:
        channel = Channel("UC1", "Solo Aging Network")
        accepted = evaluate_channel_topic(
            channel, "solo aging", ["living alone"],
            ["Solo aging planning", "Living alone tips", "Living alone support"] + ["Garden tools review"] * 17,
            "balanced",
        )
        rejected = evaluate_channel_topic(
            channel, "solo aging", ["living alone"],
            ["Solo aging planning"] + ["Garden tools review"] * 19, "balanced",
        )
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.topic_matches, 3)
        self.assertTrue(rejected.identity == "strong")
        self.assertFalse(rejected.accepted)
        self.assertIn("insufficient matching-video evidence", rejected.reason)

    def test_strict_identity_exception_and_broad_diversity_boundary(self) -> None:
        strict = evaluate_channel_topic(
            Channel("UC1", "Solo Aging Network"), "solo aging", ["living alone"],
            ["Solo aging planning", "Living alone tips"] * 3 + ["Garden tools review"] * 14,
            "strict",
        )
        broad_single = evaluate_channel_topic(
            Channel("UC2", "Unrelated"), "retirement", ["senior life"],
            ["Senior Life"] * 2 + ["Garden tools review"] * 18, "broad",
        )
        self.assertTrue(strict.accepted)
        self.assertEqual(strict.topic_coverage, 0.30)
        self.assertFalse(broad_single.accepted)

    def test_below_threshold_reason_is_explicit(self) -> None:
        result = evaluate_channel_topic(
            Channel("UC1", "Unrelated"), "retirement", ["living alone"],
            self._diverse_titles(3), "balanced",
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "Coverage below Balanced minimum.")

    def test_balanced_boundaries(self) -> None:
        self.assertTrue(evaluate_channel_topic(Channel("UC1", "Gardening"), "retirement", ["living alone"], titles(4), "balanced").accepted)
        self.assertFalse(evaluate_channel_topic(Channel("UC2", "Gardening"), "retirement", ["living alone"], titles(3), "balanced").accepted)
        self.assertEqual(DISCOVERY_TOPIC_SAMPLE_SIZE, 20)

    def test_mode_boundaries(self) -> None:
        self.assertTrue(evaluate_channel_topic(Channel("UC1", "Gardening"), "retirement", ["living alone"], titles(8), "strict").accepted)
        self.assertFalse(evaluate_channel_topic(Channel("UC2", "Gardening"), "retirement", ["living alone"], titles(7), "strict").accepted)
        self.assertTrue(evaluate_channel_topic(Channel("UC3", "Gardening"), "retirement", ["living alone"], titles(2), "broad").accepted)
        self.assertFalse(evaluate_channel_topic(Channel("UC4", "Gardening"), "retirement", ["living alone"], titles(1), "broad").accepted)

    def test_identity_exception_requires_floor(self) -> None:
        channel = Channel("UC1", "Solo Aging Life", description="Advice for seniors aging alone and planning retirement independently")
        relevant_titles = ["Solo aging planning", "Living alone tips"] * 3 + ["Garden tools review"] * 14
        low_titles = ["Solo aging planning"] + ["Garden tools review"] * 19
        self.assertTrue(evaluate_channel_topic(channel, "solo aging", ["living alone"], relevant_titles, "balanced").accepted)
        self.assertFalse(evaluate_channel_topic(channel, "solo aging", ["living alone"], low_titles, "balanced").accepted)

    def test_related_terms_and_generic_token_protection(self) -> None:
        related = ["living alone", "aging alone", "solo retirement"]
        related_titles = ["Living alone after 60"] * 7 + ["Aging alone after 60"] * 7 + ["Money news"] * 6
        self.assertTrue(evaluate_channel_topic(Channel("UC1", "Life"), "solo aging", related, related_titles, "balanced").accepted)
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
            Channel("UC1", "Unrelated"), "solo aging", ["aging alone"], ["The Reality of Solo Aging"] * 10 + ["Aging Alone Planning"] * 10, "balanced"
        )
        related = evaluate_channel_topic(
            Channel("UC2", "Unrelated"), "aging", ["aging alone", "solo retirement"], ["The Reality of Aging Alone"] * 10 + ["Solo Retirement Planning"] * 10, "balanced"
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
        sample = lambda count: ["Living alone after 65" if index % 2 == 0 else "Aging alone after 65" for index in range(count)] + ["Garden tour"] * (20 - count)
        self.assertFalse(evaluate_channel_topic(channel, "solo aging", ["living alone", "aging alone"], sample(3), "balanced").accepted)
        self.assertTrue(evaluate_channel_topic(channel, "solo aging", ["living alone", "aging alone"], sample(4), "balanced").accepted)
        self.assertTrue(evaluate_channel_topic(channel, "solo aging", ["living alone", "aging alone"], sample(8), "strict").accepted)

    def test_high_confidence_linguistic_variants_preserve_profile_label(self) -> None:
        cases = (
            ("Solo Agers: The Invisible Majority", "solo aging"),
            ("Solo Ager Support Group", "solo aging"),
            ("Unique Challenges Ages Alone", "aging alone"),
            ("Unique Challenges Age Alone", "aging alone"),
            ("Advice for Older Adults", "older adult"),
            ("Support for Seniors", "senior"),
            ("Widows Finding Community", "widow"),
            ("Single Seniors After Retirement", "single senior"),
        )
        for title, concept in cases:
            self.assertEqual(match_topic_concepts(title, [concept]), [concept], title)

    def test_variant_matching_does_not_expand_generic_or_partial_concepts(self) -> None:
        self.assertEqual(match_topic_concepts("Healthy Aging Study", ["aging"]), [])
        self.assertEqual(match_topic_concepts("Living Alone", ["living"]), [])
        self.assertEqual(match_topic_concepts("Solo", ["solo aging"]), [])
        self.assertEqual(match_topic_concepts("Ager", ["solo aging"]), [])
        self.assertEqual(match_topic_concepts("55+ Retirement Lifestyle", ["solo aging"]), [])
        self.assertEqual(match_topic_concepts("Senior Advice", ["solo aging"]), [])

    def test_variant_identity_uses_original_profile_concept(self) -> None:
        result = evaluate_channel_topic(
            Channel("UC1", "Solo Agers Network"),
            "solo aging",
            [],
            ["Unrelated gardening"] * 20,
            "balanced",
        )
        self.assertEqual(result.identity, "strong")
        self.assertIn("solo aging", result.identity_evidence)

    def test_real_positive_variant_fixture_improves_coverage_and_acceptance(self) -> None:
        titles = [
            "Solo Agers: The Invisible Majority",
            "Technology for Comfort, Conversation, and Care for Solo Agers",
            "Caregiving Challenges and Opportunities for Solo Agers",
            "Unique Challenges Aging Alone",
            "Solo Aging Membership Club",
            "Solo Aging vs Traditional Family Structures",
            "Kitchen tools review",
            "A walk through my old town",
            "Favorite books this month",
            "Morning routine vlog",
            "Family recipe collection",
        ]
        after = evaluate_channel_topic(Channel("UC1", "Solo Agers Network"), "solo aging", ["aging alone"], titles, "balanced")
        literal_before = sum("solo aging" in title.casefold() for title in titles)
        self.assertEqual(literal_before, 2)
        self.assertEqual(after.topic_matches, 6)
        self.assertLess(literal_before / len(titles), after.topic_coverage)
        self.assertTrue(after.accepted)
        self.assertEqual(after.title_evidence[0].matched_concepts, ["solo aging"])

    def test_real_mixed_content_fixture_remains_rejected_balanced(self) -> None:
        titles = [
            "Solo Aging Membership Club",
            "Unique Challenges Aging Alone",
            "Living Alone After Retirement",
            "I Had to Go to the ER by Myself",
            "Stop Giving People Access to You",
            "The 55+ Retirement Lifestyle Trap",
            "When You Have Nothing Left to Give",
            "I Visited My Old Town",
            "They're Wrong",
            "A Day in My Personal Story",
            "Family Dinner and Memories",
            "My Favorite Kitchen Tools",
            "A Quiet Morning Walk",
            "Traveling to See Friends",
            "What I Learned This Week",
            "How I Organize My Home",
            "A New Book Review",
            "Trying a New Recipe",
            "Weekend Gardening",
            "A Personal Update",
        ]
        result = evaluate_channel_topic(
            Channel("UC1", "Personal Stories"),
            "solo aging",
            ["aging alone", "living alone"],
            titles,
            "balanced",
        )
        self.assertEqual(result.topic_matches, 3)
        self.assertLess(result.topic_coverage, 0.40)
        self.assertFalse(result.accepted)


if __name__ == "__main__":
    unittest.main()
