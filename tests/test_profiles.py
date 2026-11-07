"""Profile creation, versioned updates, and derived segments."""
import pytest

from apers.models import Profile, ProfileStatus
from apers.profiles import ProfileStore, derive_segments


def test_derive_segments_from_declared_interest():
    assert derive_segments({"interest": "cooking"}, {}) == ["food-lover"]
    assert derive_segments({"interest": "fitness"}, {}) == ["fitness-enthusiast"]


def test_derive_segments_inferred_needs_confidence():
    assert derive_segments({}, {"baking": 0.85}) == ["food-lover"]
    assert derive_segments({}, {"baking": 0.5}) == []  # below 0.7 threshold


def test_create_assigns_segments_and_indexes():
    store = ProfileStore()
    profile = store.create(Profile(user_id="u1", declared={"interest": "cooking"}))
    assert profile.segments == ["food-lover"]
    assert store.members("food-lover") == {"u1"}


def test_create_duplicate_rejected():
    store = ProfileStore()
    store.create(Profile(user_id="u1"))
    with pytest.raises(ValueError, match="already exists"):
        store.create(Profile(user_id="u1"))


def test_create_rejects_bad_inferred_confidence():
    store = ProfileStore()
    with pytest.raises(ValueError, match="confidence must be in 0..1"):
        store.create(Profile(user_id="u1", inferred={"cooking": 1.5}))


def test_update_supersedes_and_reindexes():
    store = ProfileStore()
    store.create(Profile(user_id="u1", declared={"interest": "cooking"}))
    updated = store.update("u1", inferred={"travel": 0.9}, now="2026-11-07")
    assert updated.version == 2
    assert updated.segments == ["food-lover", "travel-planner"]
    assert store.version("u1", 1).superseded_by == 2
    assert store.latest("u1") is updated
    assert store.members("travel-planner") == {"u1"}
    assert len(store.history("u1")) == 2


def test_update_unknown_profile_rejected():
    store = ProfileStore()
    with pytest.raises(ValueError, match="unknown profile"):
        store.update("ghost", declared={"interest": "cooking"})


def test_cold_start_detection():
    assert Profile(user_id="u1").is_cold_start is True
    assert Profile(user_id="u1", declared={"interest": "cooking"}).is_cold_start is False
    assert Profile(user_id="u1", inferred={"cooking": 0.8}).is_cold_start is False


def test_erased_profile_validation():
    profile = Profile(user_id="u1", status=ProfileStatus.ERASED,
                      declared={"interest": "cooking"})
    assert any("erased profile" in e for e in profile.validate())
