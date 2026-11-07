"""Experiment integration: deterministic variant assignment, exposure
logging, and per-variant engagement readouts.

Assignment is a stable hash of (experiment_id, user_id) mapped to
buckets of 100: 0-33 control, 34-66 personalized, 67-99
personalized+diversity. The same user always lands in the same variant
for a given experiment, with no stored assignment table to drift.
Exposure events record which variant served which item and whether the
user engaged; readouts report per-variant exposure, engagement, and
rate, and flag variants below the minimum sample size so nobody draws
conclusions from noise.
"""
from __future__ import annotations

import hashlib

from apers.models import ExposureEvent, Variant

# Bucket boundaries (percentiles of 100): control / personalized /
# personalized+diversity.
BUCKETS: list[tuple[int, Variant]] = [
    (34, Variant.CONTROL),
    (67, Variant.PERSONALIZED),
    (100, Variant.PERSONALIZED_DIVERSITY),
]

MIN_SAMPLE_FOR_READOUT = 30


def assign_variant(experiment_id: str, user_id: str) -> Variant:
    """Deterministic variant assignment from a stable hash."""
    digest = hashlib.sha256(f"{experiment_id}:{user_id}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    for upper, variant in BUCKETS:
        if bucket < upper:
            return variant
    return Variant.PERSONALIZED_DIVERSITY  # unreachable; keeps mypy honest


class ExperimentLog:
    """In-memory exposure log, keyed by experiment."""

    def __init__(self) -> None:
        self.events: list[ExposureEvent] = []

    def log_exposure(self, event: ExposureEvent) -> ExposureEvent:
        self.events.append(event)
        return event

    def readout(self, experiment_id: str) -> dict:
        """Per-variant engagement readout. Variants with fewer than
        MIN_SAMPLE_FOR_READOUT exposures are flagged underpowered."""
        variants: dict[str, dict] = {}
        for variant in Variant:
            exposed = [e for e in self.events
                       if e.experiment_id == experiment_id and e.variant is variant]
            engaged = sum(1 for e in exposed if e.engaged)
            variants[variant.value] = {
                "exposures": len(exposed),
                "engagements": engaged,
                "engagement_rate": round(engaged / len(exposed), 4) if exposed else 0.0,
                "underpowered": len(exposed) < MIN_SAMPLE_FOR_READOUT,
            }
        total = sum(v["exposures"] for v in variants.values())
        return {"experiment_id": experiment_id, "total_exposures": total,
                "min_sample": MIN_SAMPLE_FOR_READOUT, "variants": variants}
