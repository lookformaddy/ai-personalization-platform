"""Feature-aware ranking: deterministic, explainable scoring with
diversity guardrails and a cold-start fallback.

Score model (all components in 0..1, weights sum to 1):
    score = w_affinity * affinity + w_popularity * popularity
          + w_recency * recency + w_exploration * exploration

* **affinity** — overlap between the profile's interest vector
  (declared interests weight 1.0, inferred attributes weight =
  confidence) and the item's feature weights, normalized by the
  item's total feature weight.
* **popularity** — the item's normalized engagement rate.
* **recency** — ``1 / (1 + age_days / 30)`` decay from ``published_at``.
* **exploration** — deterministic jitter ``hash(user_id, item_id)``
  mapped to [0, 1); used only for cold-start profiles and the
  diversity variant, so new items get a chance without randomness.

Control-variant ranking ignores the profile entirely (popularity +
recency only). Diversity guardrails cap how many items from one
category may appear in the top-N; capped-out items are reported, and
the list is backfilled from the remaining pool so the caller still
gets ``limit`` items when enough exist. Every scored item carries its
full component breakdown, so any ranking is explainable after the
fact.
"""
from __future__ import annotations

import hashlib
from datetime import date

from apers.models import Item, Profile, RankingDecision, ScoredItem, Variant

WEIGHTS = {"affinity": 0.50, "popularity": 0.25, "recency": 0.25,
           "exploration": 0.0}
COLD_START_WEIGHTS = {"affinity": 0.0, "popularity": 0.55,
                      "recency": 0.35, "exploration": 0.10}
CONTROL_WEIGHTS = {"affinity": 0.0, "popularity": 0.60,
                   "recency": 0.40, "exploration": 0.0}

DEFAULT_MAX_PER_CATEGORY = 2
DIVERSITY_MAX_PER_CATEGORY = 1


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _exploration_jitter(user_id: str, item_id: str) -> float:
    """Deterministic [0, 1) jitter from (user, item) — stable across
    runs, so exploration is reproducible, not random."""
    digest = hashlib.sha256(f"{user_id}:{item_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _affinity(profile: Profile, item: Item) -> float:
    """Interest overlap normalized by the item's feature weight."""
    interest: dict[str, float] = {}
    for key in profile.declared.get("interest", "").split(","):
        key = key.strip()
        if key:
            interest[key] = 1.0
    for key, confidence in profile.inferred.items():
        interest[key] = max(interest.get(key, 0.0), confidence)
    total = sum(item.features.values())
    if not interest or total <= 0:
        return 0.0
    matched = sum(interest.get(k, 0.0) * w for k, w in item.features.items())
    return min(1.0, matched / total)


def _recency(item: Item, today: date) -> float:
    published = _parse_date(item.published_at)
    if published is None:
        return 0.5  # unknown age: neutral, neither boosted nor buried
    age_days = max(0, (today - published).days)
    return 1.0 / (1.0 + age_days / 30.0)


def score_item(profile: Profile, item: Item, today: date,
               weights: dict[str, float]) -> ScoredItem:
    """Score one item; the breakdown sums exactly to the score."""
    affinity = _affinity(profile, item) if weights["affinity"] > 0 else 0.0
    exploration = (_exploration_jitter(profile.user_id, item.item_id)
                   if weights["exploration"] > 0 else 0.0)
    breakdown = {
        "affinity": round(weights["affinity"] * affinity, 6),
        "popularity": round(weights["popularity"] * item.popularity, 6),
        "recency": round(weights["recency"] * _recency(item, today), 6),
        "exploration": round(weights["exploration"] * exploration, 6),
    }
    return ScoredItem(item_id=item.item_id,
                      score=round(sum(breakdown.values()), 6),
                      breakdown=breakdown,
                      cold_start=profile.is_cold_start)


def _apply_category_cap(scored: list[ScoredItem], items: dict[str, Item],
                        limit: int, max_per_category: int
                        ) -> tuple[list[ScoredItem], list[str]]:
    """Greedy top-N selection honoring the per-category cap. Returns
    (selected, capped_out_item_ids). Backfills from skipped items so
    the caller still gets ``limit`` items when the pool allows."""
    selected: list[ScoredItem] = []
    counts: dict[str, int] = {}
    skipped: list[ScoredItem] = []
    for entry in scored:
        category = items[entry.item_id].category
        if counts.get(category, 0) >= max_per_category:
            skipped.append(entry)
            continue
        counts[category] = counts.get(category, 0) + 1
        selected.append(entry)
        if len(selected) >= limit:
            break
    capped_out = [e.item_id for e in skipped]
    # Backfill: if the cap left slots empty, take the best skipped items.
    if len(selected) < limit:
        for entry in skipped:
            selected.append(entry)
            if len(selected) >= limit:
                break
    return selected, capped_out


def rank(profile: Profile, items: list[Item], today: str,
         limit: int = 10, variant: Variant = Variant.PERSONALIZED,
         max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
         ) -> tuple[list[ScoredItem], list[str], RankingDecision]:
    """Rank items for a profile. Returns (ranked, capped_out, decision).

    ``variant`` selects the scoring weights and cap: CONTROL ignores
    the profile; PERSONALIZED_DIVERSITY tightens the category cap to
    one per category and adds exploration jitter. Cold-start profiles
    always use the cold-start weights regardless of variant (except
    CONTROL, which is profile-blind by definition).
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    for item in items:
        errors = item.validate()
        if errors:
            raise ValueError(f"item {item.item_id}: " + "; ".join(errors))
    today_date = _parse_date(today) or date(2026, 11, 7)

    if variant is Variant.CONTROL:
        weights = CONTROL_WEIGHTS
    elif profile.is_cold_start:
        weights = COLD_START_WEIGHTS
    else:
        weights = WEIGHTS
    if variant is Variant.PERSONALIZED_DIVERSITY:
        weights = {**weights, "exploration": 0.05,
                   "popularity": weights["popularity"] - 0.05}
        max_per_category = min(max_per_category, DIVERSITY_MAX_PER_CATEGORY)

    scored = [score_item(profile, item, today_date, weights) for item in items]
    # Deterministic ordering: score desc, then item_id asc (stable ties).
    scored.sort(key=lambda s: (-s.score, s.item_id))
    by_id = {item.item_id: item for item in items}
    selected, capped_out = _apply_category_cap(scored, by_id, limit, max_per_category)
    for i, entry in enumerate(selected, start=1):
        entry.rank = i
    decision = RankingDecision(
        user_id=profile.user_id, profile_version=profile.version,
        purpose=_purpose_for_variant(variant), variant=variant,
        item_ids=[e.item_id for e in selected],
        cold_start=profile.is_cold_start, decided_at=today)
    return selected, capped_out, decision


def _purpose_for_variant(variant: Variant):
    """Ranking is a personalization act for every variant except
    CONTROL, which uses no profile data (analytics-grade)."""
    from apers.models import Purpose
    return Purpose.ANALYTICS if variant is Variant.CONTROL else Purpose.PERSONALIZATION
