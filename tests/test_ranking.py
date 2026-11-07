"""Feature-aware ranking: scoring math, explainability, diversity
caps, control variant, and the cold-start fallback."""
import pytest
from datetime import date

from apers.models import Item, Profile, Variant
from apers.ranking import rank, score_item, WEIGHTS

TODAY = "2026-11-07"

CATALOG = [
    Item("it-01", "30-minute weeknight pasta", "food",
         {"cooking": 0.9, "budget": 0.5}, 0.82, "2026-11-05"),
    Item("it-02", "Sourdough starter guide", "food",
         {"cooking": 0.8, "baking": 0.9}, 0.64, "2026-10-20"),
    Item("it-03", "5k training plan", "fitness",
         {"running": 0.9, "fitness": 0.8}, 0.77, "2026-11-02"),
    Item("it-04", "Strength basics", "fitness",
         {"fitness": 0.85}, 0.58, "2026-09-15"),
    Item("it-05", "Lisbon city break", "travel",
         {"travel": 0.9}, 0.71, "2026-11-01"),
]

FOODIE = Profile(user_id="u1", declared={"interest": "cooking"},
                 inferred={"baking": 0.85}, segments=["food-lover"])


def test_breakdown_sums_to_score():
    scored = score_item(FOODIE, CATALOG[0], date(2026, 11, 7), WEIGHTS)
    assert abs(sum(scored.breakdown.values()) - scored.score) < 1e-6
    assert scored.breakdown["affinity"] > 0  # cooking matches


def test_personalized_ranking_puts_matching_item_first():
    ranked, capped_out, decision = rank(FOODIE, CATALOG, TODAY, limit=3)
    # it-02 matches both declared cooking and inferred baking (0.85),
    # so it out-scores it-01 despite it-01's higher popularity/recency.
    assert ranked[0].item_id == "it-02"
    assert ranked[1].item_id == "it-01"
    assert [s.rank for s in ranked] == [1, 2, 3]
    assert decision.profile_version == 1
    assert decision.cold_start is False
    assert decision.variant is Variant.PERSONALIZED


def test_control_variant_ignores_profile():
    ranked, _, _ = rank(FOODIE, CATALOG, TODAY, limit=3, variant=Variant.CONTROL)
    # Control is popularity+recency only: it-01 (pop .82, fresh) still
    # leads, but affinity must contribute zero to every breakdown.
    assert all(s.breakdown["affinity"] == 0 for s in ranked)
    assert ranked[0].item_id == "it-01"


def test_category_cap_and_backfill():
    # Default cap is 2 per category; top-3 would otherwise be all food
    # if food dominated. With limit=5 the cap binds and backfills.
    ranked, capped_out, _ = rank(FOODIE, CATALOG, TODAY, limit=5,
                                 max_per_category=1)
    categories = [next(i.category for i in CATALOG if i.item_id == s.item_id)
                  for s in ranked]
    assert len(ranked) == 5  # backfilled from capped-out pool
    assert categories.count("food") <= 2  # cap 1 + one backfilled item
    assert capped_out  # at least one item was capped out


def test_diversity_variant_caps_at_one_per_category():
    ranked, capped_out, _ = rank(FOODIE, CATALOG, TODAY, limit=3,
                                 variant=Variant.PERSONALIZED_DIVERSITY)
    categories = [next(i.category for i in CATALOG if i.item_id == s.item_id)
                  for s in ranked]
    assert len(set(categories)) == len(categories)  # all distinct
    assert capped_out


def test_cold_start_uses_popularity_and_exploration():
    newbie = Profile(user_id="u-new")
    ranked, _, decision = rank(newbie, CATALOG, TODAY, limit=3)
    assert decision.cold_start is True
    assert all(s.breakdown["affinity"] == 0 for s in ranked)
    assert all(s.cold_start for s in ranked)
    # Popularity leader it-01 is fresh too, so it tops the cold-start list.
    assert ranked[0].item_id == "it-01"


def test_ranking_is_deterministic():
    first, _, _ = rank(FOODIE, CATALOG, TODAY, limit=5)
    second, _, _ = rank(FOODIE, CATALOG, TODAY, limit=5)
    assert [s.item_id for s in first] == [s.item_id for s in second]
    assert [s.score for s in first] == [s.score for s in second]


def test_recency_decay_prefers_fresh_items():
    old = Item("old", "Old post", "food", {"cooking": 0.9}, 0.90, "2026-01-01")
    fresh = Item("fresh", "Fresh post", "food", {"cooking": 0.9}, 0.50, "2026-11-06")
    ranked, _, _ = rank(FOODIE, [old, fresh], TODAY, limit=2)
    assert ranked[0].item_id == "fresh"  # recency outweighs the popularity gap


def test_invalid_item_rejected():
    bad = Item("bad", "Bad", "food", {}, 1.5, "2026-11-01")
    with pytest.raises(ValueError, match="popularity"):
        rank(FOODIE, [bad], TODAY)


def test_limit_validation():
    with pytest.raises(ValueError, match="limit must be >= 1"):
        rank(FOODIE, CATALOG, TODAY, limit=0)
