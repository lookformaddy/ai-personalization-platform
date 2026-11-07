"""CLI: create a profile, manage consent, rank a synthetic catalog,
assign an experiment variant, erase a profile, and print a report.

Usage:
    PYTHONPATH=src python -m apers.cli profile --user-id u1 --interest cooking
    PYTHONPATH=src python -m apers.cli consent --user-id u1 --purpose personalization --grant
    PYTHONPATH=src python -m apers.cli rank --user-id u1 --limit 5
    PYTHONPATH=src python -m apers.cli assign --user-id u1 --experiment exp-1
    PYTHONPATH=src python -m apers.cli erase --user-id u1
    PYTHONPATH=src python -m apers.cli report
"""
from __future__ import annotations

import argparse
import json

from apers.consent import ConsentLedger, hash_pii, usable_profile
from apers.erasure import erase_profile
from apers.experiments import ExperimentLog, assign_variant
from apers.models import Item, Profile, Purpose
from apers.profiles import ProfileStore
from apers.ranking import rank
from apers.reporting import personalization_report

TODAY = "2026-11-07"

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apers",
                                     description="Consent-Aware Personalization Platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    prof = sub.add_parser("profile", help="Create a profile with declared interests")
    prof.add_argument("--user-id", required=True)
    prof.add_argument("--interest", default="cooking")
    prof.add_argument("--email", default="")

    con = sub.add_parser("consent", help="Grant or withdraw purpose consent")
    con.add_argument("--user-id", required=True)
    con.add_argument("--purpose", required=True,
                     choices=[p.value for p in Purpose])
    con.add_argument("--grant", action="store_true")
    con.add_argument("--withdraw", action="store_true")

    rk = sub.add_parser("rank", help="Rank the synthetic catalog for a user")
    rk.add_argument("--user-id", required=True)
    rk.add_argument("--interest", default="cooking")
    rk.add_argument("--limit", type=int, default=5)
    rk.add_argument("--variant", default="personalized",
                    choices=["control", "personalized", "personalized+diversity"])
    rk.add_argument("--no-consent", action="store_true",
                    help="Rank without granting consent (shows the fail-closed block)")

    asg = sub.add_parser("assign", help="Assign an experiment variant")
    asg.add_argument("--user-id", required=True)
    asg.add_argument("--experiment", default="exp-1")

    er = sub.add_parser("erase", help="Erase a profile (right to be forgotten)")
    er.add_argument("--user-id", required=True)

    sub.add_parser("report", help="Print an empty personalization report template")
    return parser


def _demo_profile(user_id: str, interest: str) -> Profile:
    return Profile(user_id=user_id, declared={"interest": interest},
                   pii_hash=hash_pii(f"{user_id}@example.com"),
                   created_at=TODAY, updated_at=TODAY)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = ProfileStore()
    ledger = ConsentLedger()
    log = ExperimentLog()

    if args.command == "profile":
        profile = _demo_profile(args.user_id, args.interest)
        store.create(profile)
        print(json.dumps({"user_id": profile.user_id, "version": profile.version,
                          "segments": profile.segments,
                          "pii_hash": profile.pii_hash}, indent=2))
        return 0

    if args.command == "consent":
        if args.grant == args.withdraw:
            print("pass exactly one of --grant / --withdraw")
            return 2
        store.create(_demo_profile(args.user_id, "cooking"))
        purpose = Purpose(args.purpose)
        record = (ledger.grant if args.grant else ledger.withdraw)(
            args.user_id, purpose, now=TODAY, source="cli")
        print(json.dumps({"user_id": args.user_id, "purpose": purpose.value,
                          "granted": record.granted}, indent=2))
        return 0

    if args.command == "rank":
        from apers.models import Variant
        store.create(_demo_profile(args.user_id, args.interest))
        if not args.no_consent:
            ledger.grant(args.user_id, Purpose.PERSONALIZATION, now=TODAY, source="cli")
        profile = store.latest(args.user_id)
        assert profile is not None
        try:
            usable = usable_profile(profile, Purpose.PERSONALIZATION, ledger)
        except PermissionError as exc:
            print(f"blocked (fail-closed): {exc}")
            return 1
        variant = Variant(args.variant)
        ranked, capped_out, decision = rank(usable, CATALOG, today=TODAY,
                                            limit=args.limit, variant=variant)
        print(json.dumps({
            "user_id": args.user_id, "variant": variant.value,
            "cold_start": decision.cold_start,
            "ranked": [{"item_id": s.item_id, "score": s.score,
                        "breakdown": s.breakdown} for s in ranked],
            "capped_out": capped_out}, indent=2))
        return 0

    if args.command == "assign":
        print(json.dumps({"user_id": args.user_id, "experiment": args.experiment,
                          "variant": assign_variant(args.experiment, args.user_id).value},
                         indent=2))
        return 0

    if args.command == "erase":
        store.create(_demo_profile(args.user_id, "cooking"))
        tombstone = erase_profile(store, args.user_id, log, now=TODAY)
        print(json.dumps({"user_id": args.user_id, "status": tombstone.status.value,
                          "version": tombstone.version,
                          "segments_after": store.members("food-lover")}, indent=2,
                         default=list))
        return 0

    if args.command == "report":
        print(json.dumps(personalization_report(store, ledger, [], {}, log), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
