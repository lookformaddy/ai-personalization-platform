"""End-to-end demo: create profiles, grant/withdraw consent, rank a
synthetic catalog across all three experiment variants, show the
cold-start fallback and diversity caps, erase a profile, and print the
personalization report plus audit verification.

All data is synthetic; all providers are deterministic. Run with:
    PYTHONPATH=src python scripts/demo.py
"""
from __future__ import annotations

import json

from apers.audit import AuditLog
from apers.consent import ConsentLedger, hash_pii, usable_profile
from apers.erasure import erase_profile
from apers.experiments import ExperimentLog, assign_variant
from apers.models import (
    ExposureEvent,
    Item,
    Profile,
    Purpose,
    Variant,
)
from apers.profiles import ProfileStore
from apers.ranking import rank
from apers.reporting import personalization_report

TODAY = "2026-11-07"
EXPERIMENT = "exp-personalization-v1"

CATALOG = [
    Item("it-01", "30-minute weeknight pasta", "food",
         {"cooking": 0.9, "budget": 0.5}, 0.82, "2026-11-05"),
    Item("it-02", "Sourdough starter guide", "food",
         {"cooking": 0.8, "baking": 0.9}, 0.64, "2026-10-20"),
    Item("it-03", "5k training plan for beginners", "fitness",
         {"running": 0.9, "fitness": 0.8}, 0.77, "2026-11-02"),
    Item("it-04", "Strength basics at home", "fitness",
         {"fitness": 0.85}, 0.58, "2026-09-15"),
    Item("it-05", "Weekend city break: Lisbon", "travel",
         {"travel": 0.9}, 0.71, "2026-11-01"),
    Item("it-06", "Packing light for a week", "travel",
         {"travel": 0.7, "budget": 0.6}, 0.49, "2026-10-10"),
    Item("it-07", "Acoustic guitar practice routine", "music",
         {"music": 0.9}, 0.66, "2026-10-28"),
    Item("it-08", "Budget travel meals", "food",
         {"cooking": 0.6, "travel": 0.5, "budget": 0.8}, 0.55, "2026-11-06"),
]


def _profile(user_id: str, **kw) -> Profile:
    return Profile(user_id=user_id, created_at=TODAY, updated_at=TODAY, **kw)


def main() -> None:
    store = ProfileStore()
    ledger = ConsentLedger()
    audit = AuditLog()
    log = ExperimentLog()
    decisions = []

    # 1. Create three synthetic profiles: a food lover, a fitness
    #    enthusiast, and a cold-start user with no signal at all.
    foodie = _profile("u-foodie", declared={"interest": "cooking"},
                      inferred={"baking": 0.85},
                      pii_hash=hash_pii("foodie@example.com"))
    athlete = _profile("u-athlete", declared={"interest": "fitness"},
                       pii_hash=hash_pii("athlete@example.com"))
    newbie = _profile("u-newbie", pii_hash=hash_pii("newbie@example.com"))
    for profile in (foodie, athlete, newbie):
        store.create(profile)
        audit.append(TODAY, "demo", "profile_created", profile.user_id,
                     f"v{profile.version} segments={','.join(profile.segments)}")
        print(f"created {profile.user_id}: segments={profile.segments} "
              f"cold_start={profile.is_cold_start}")

    # 2. Versioned update: the foodie adds a travel interest; v1 is
    #    superseded, v2 carries both segments.
    updated = store.update("u-foodie", declared={"interest": "cooking"},
                           inferred={"travel": 0.9}, now=TODAY)
    print(f"updated u-foodie -> v{updated.version}: segments={updated.segments} "
          f"(v1 superseded_by={store.version('u-foodie', 1).superseded_by})")

    # 3. Consent: foodie + athlete consent to personalization; the
    #    newbie consents only to analytics. Ranking without consent
    #    must fail closed.
    ledger.grant("u-foodie", Purpose.PERSONALIZATION, now=TODAY, source="demo")
    ledger.grant("u-athlete", Purpose.PERSONALIZATION, now=TODAY, source="demo")
    ledger.grant("u-newbie", Purpose.ANALYTICS, now=TODAY, source="demo")
    try:
        usable_profile(store.latest("u-newbie"),
                       Purpose.PERSONALIZATION, ledger)
    except PermissionError as exc:
        print(f"fail-closed ranking block (expected): {exc}")
        audit.append(TODAY, "demo", "ranking_blocked_no_consent", "u-newbie",
                     "personalization")

    # 4. Rank for the foodie under all three variants.
    for variant in Variant:
        usable = usable_profile(store.latest("u-foodie"),
                                Purpose.PERSONALIZATION, ledger)
        ranked, capped_out, decision = rank(usable, CATALOG, today=TODAY,
                                            limit=5, variant=variant)
        decisions.append(decision)
        audit.append(TODAY, "demo", "ranking_decided", "u-foodie",
                     f"variant={variant.value} top={ranked[0].item_id}")
        top = ranked[0]
        print(f"rank u-foodie [{variant.value}]: top={top.item_id} "
              f"score={top.score} breakdown={top.breakdown} "
              f"capped_out={capped_out}")
        assert decision.profile_version == 2  # ranking used the newest version

    # 5. Cold-start fallback: the newbie ranks on popularity + recency
    #    + exploration only (analytics purpose, no profile signal).
    usable = usable_profile(store.latest("u-newbie"), Purpose.ANALYTICS, ledger)
    ranked, _, decision = rank(usable, CATALOG, today=TODAY, limit=5,
                               variant=Variant.PERSONALIZED)
    decisions.append(decision)
    print(f"rank u-newbie [cold-start]: cold_start={decision.cold_start} "
          f"top={ranked[0].item_id} score={ranked[0].score}")

    # 6. Experiment: deterministic assignment + exposure logging with
    #    engagement, then the per-variant readout.
    assignments: dict[str, int] = {}
    for i in range(60):
        uid = f"demo-user-{i:02d}"
        variant = assign_variant(EXPERIMENT, uid)
        assignments[variant.value] = assignments.get(variant.value, 0) + 1
        # Deterministic synthetic engagement: personalized variants
        # engage more often (hash-driven, no randomness).
        import hashlib
        digest = int(hashlib.sha256(f"{uid}:{variant.value}".encode()).hexdigest()[:8], 16)
        engaged = (digest % 10) < (6 if variant is not Variant.CONTROL else 3)
        log.log_exposure(ExposureEvent(uid, EXPERIMENT, variant, "it-01",
                                       engaged, TODAY))
    print(f"experiment assignments (60 users): {assignments}")
    readout = log.readout(EXPERIMENT)
    print("experiment readout:")
    print(json.dumps(readout, indent=2))

    # 7. Erasure: the athlete exercises the right to be forgotten.
    #    Segments drop them, exposures are anonymized, ranking stops.
    tombstone = erase_profile(store, "u-athlete", log, now=TODAY)
    audit.append(TODAY, "u-athlete", "profile_erased", "u-athlete",
                 f"tombstone v{tombstone.version}")
    print(f"erased u-athlete: status={tombstone.status.value} "
          f"segments={tombstone.segments} "
          f"fitness-enthusiast members={sorted(store.members('fitness-enthusiast'))}")
    assert not store.members("fitness-enthusiast")
    try:
        usable_profile(store.latest("u-athlete"), Purpose.PERSONALIZATION, ledger)
    except PermissionError as exc:
        print(f"ranking after erasure blocked (expected): {exc}")

    # 8. Report + audit verification.
    report = personalization_report(store, ledger, decisions,
                                    {it.item_id: it for it in CATALOG},
                                    log, EXPERIMENT)
    print("\npersonalization report:")
    print(json.dumps(report, indent=2))
    ok, message = audit.verify()
    print(f"\naudit: valid={ok} ({message})")
    assert ok and report["profiles_erased"] == 1
    assert report["consent_coverage_pct"] == 50.0  # 1 of 2 active profiles
    print("\ndemo OK: consent-gated ranking, variant readouts, cold-start "
          "fallback, erasure propagation, audit chain intact.")


if __name__ == "__main__":
    main()
