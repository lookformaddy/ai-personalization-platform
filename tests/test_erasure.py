"""Erasure: tombstoning, segment propagation, exposure anonymization,
and the post-erasure blocks."""
import pytest

from apers.consent import ConsentLedger, usable_profile
from apers.erasure import erase_profile
from apers.experiments import ExperimentLog
from apers.models import ExposureEvent, Profile, ProfileStatus, Purpose, Variant
from apers.profiles import ProfileStore


def _store_with_user() -> ProfileStore:
    store = ProfileStore()
    store.create(Profile(user_id="u1", declared={"interest": "cooking"},
                         inferred={"baking": 0.8}))
    return store


def test_erase_tombstones_and_clears_attributes():
    store = _store_with_user()
    tombstone = erase_profile(store, "u1", now="2026-11-07")
    assert tombstone.status is ProfileStatus.ERASED
    assert tombstone.version == 2
    assert tombstone.declared == {} and tombstone.inferred == {}
    assert tombstone.segments == []
    assert tombstone.pii_hash  # hashed reference retained, raw PII gone
    assert store.version("u1", 1).superseded_by == 2


def test_erase_propagates_to_segments():
    store = _store_with_user()
    assert store.members("food-lover") == {"u1"}
    erase_profile(store, "u1", now="2026-11-07")
    assert store.members("food-lover") == set()


def test_erase_anonymizes_exposure_events():
    store = _store_with_user()
    log = ExperimentLog()
    log.log_exposure(ExposureEvent("u1", "exp-1", Variant.PERSONALIZED,
                                   "it-01", True, "2026-11-07"))
    erase_profile(store, "u1", log, now="2026-11-07")
    assert log.events[0].user_id.startswith("erased:")
    assert log.readout("exp-1")["total_exposures"] == 1  # counts preserved


def test_erase_unknown_and_double_erase_rejected():
    store = ProfileStore()
    with pytest.raises(ValueError, match="unknown profile"):
        erase_profile(store, "ghost")
    store = _store_with_user()
    erase_profile(store, "u1", now="2026-11-07")
    with pytest.raises(ValueError, match="already erased"):
        erase_profile(store, "u1", now="2026-11-07")


def test_erased_profile_cannot_be_used_or_updated():
    store = _store_with_user()
    erase_profile(store, "u1", now="2026-11-07")
    ledger = ConsentLedger()
    ledger.grant("u1", Purpose.PERSONALIZATION)
    with pytest.raises(PermissionError, match="erased"):
        usable_profile(store.latest("u1"), Purpose.PERSONALIZATION, ledger)
    with pytest.raises(ValueError, match="erased"):
        store.update("u1", declared={"interest": "travel"})
