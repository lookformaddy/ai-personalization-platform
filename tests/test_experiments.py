"""Experiment assignment, exposure logging, and readouts."""
from apers.experiments import ExperimentLog, assign_variant
from apers.models import ExposureEvent, Variant


def test_assignment_is_deterministic_and_covers_all_variants():
    first = assign_variant("exp-1", "user-42")
    assert assign_variant("exp-1", "user-42") is first
    variants = {assign_variant("exp-1", f"user-{i}") for i in range(200)}
    assert variants == set(Variant)  # all three buckets are reachable


def test_assignment_differs_across_experiments():
    # Same user, different experiment: assignment is per-experiment.
    a = assign_variant("exp-1", "user-42")
    b = assign_variant("exp-2", "user-42")
    assert isinstance(a, Variant) and isinstance(b, Variant)
    # Distribution over many users is roughly balanced across buckets.
    counts = {v: 0 for v in Variant}
    for i in range(300):
        counts[assign_variant("exp-1", f"u-{i}")] += 1
    for variant, count in counts.items():
        assert 60 <= count <= 140, (variant, count)  # ~100 each, loose band


def test_readout_rates_and_underpowered_flag():
    log = ExperimentLog()
    for i in range(10):
        log.log_exposure(ExposureEvent(f"u-{i}", "exp-1", Variant.CONTROL,
                                       "it-01", engaged=(i < 3), recorded_at="2026-11-07"))
    readout = log.readout("exp-1")
    control = readout["variants"]["control"]
    assert control["exposures"] == 10
    assert control["engagements"] == 3
    assert control["engagement_rate"] == 0.3
    assert control["underpowered"] is True  # < 30 exposures
    assert readout["total_exposures"] == 10
    assert readout["variants"]["personalized"]["exposures"] == 0


def test_readout_powered_when_sample_large_enough():
    log = ExperimentLog()
    for i in range(40):
        log.log_exposure(ExposureEvent(f"u-{i}", "exp-1", Variant.PERSONALIZED,
                                       "it-01", engaged=True, recorded_at="2026-11-07"))
    assert log.readout("exp-1")["variants"]["personalized"]["underpowered"] is False
