"""Reporting: per-category exposure, per-variant engagement, consent
coverage, and erasure counts — computed from the same stores the API
serves, so the report can never drift from the live state.
"""
from __future__ import annotations

from apers.consent import ConsentLedger
from apers.experiments import ExperimentLog
from apers.models import Item, Profile, Purpose, RankingDecision
from apers.profiles import ProfileStore


def personalization_report(store: ProfileStore, ledger: ConsentLedger,
                           decisions: list[RankingDecision],
                           items: dict[str, Item],
                           experiment_log: ExperimentLog | None = None,
                           experiment_id: str = "") -> dict:
    """Aggregate report over profiles, consent, ranking decisions,
    and (optionally) one experiment's readout."""
    profiles = [store.latest(uid) for uid in store.all_user_ids()]
    profiles = [p for p in profiles if p is not None]
    active = [p for p in profiles if p.status.value == "active"]
    erased = [p for p in profiles if p.status.value == "erased"]

    consented = sum(1 for p in active
                    if ledger.is_allowed(p.user_id, Purpose.PERSONALIZATION))
    cold_start = sum(1 for p in active if p.is_cold_start)

    # Per-category exposure across all recorded ranking decisions.
    category_exposure: dict[str, int] = {}
    total_ranked = 0
    for decision in decisions:
        for item_id in decision.item_ids:
            item = items.get(item_id)
            if item is None:
                continue
            category_exposure[item.category] = category_exposure.get(item.category, 0) + 1
            total_ranked += 1
    exposure_share = {
        category: round(count / total_ranked, 4) if total_ranked else 0.0
        for category, count in sorted(category_exposure.items())
    }

    report: dict = {
        "profiles_total": len(profiles),
        "profiles_active": len(active),
        "profiles_erased": len(erased),
        "consent_personalization": consented,
        "consent_coverage_pct": round(100.0 * consented / len(active), 1) if active else 0.0,
        "cold_start_profiles": cold_start,
        "ranking_decisions": len(decisions),
        "items_ranked_total": total_ranked,
        "category_exposure": category_exposure,
        "category_exposure_share": exposure_share,
        "segments": {segment: len(members)
                     for segment, members in sorted(store.segment_index.items())
                     if members},
    }
    if experiment_log is not None and experiment_id:
        report["experiment"] = experiment_log.readout(experiment_id)
    return report
