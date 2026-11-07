"""User context profiles: creation, versioned updates, and derived
segment membership.

A profile update never mutates history: it creates a new version and
marks the previous version superseded, so any ranking decision can be
traced to the exact profile version that produced it. Segments are
derived deterministically from declared + inferred attributes via
:py:data:`SEGMENT_RULES`; the store keeps a reverse index
(segment -> user ids) so erasure can propagate to derived segments.
"""
from __future__ import annotations

from apers.models import Profile, ProfileStatus

# Declared attribute value -> segment. Inferred attributes map the same
# way when their confidence meets INFERRED_CONFIDENCE_MIN.
SEGMENT_RULES: dict[str, dict[str, str]] = {
    "interest": {
        "cooking": "food-lover",
        "recipes": "food-lover",
        "baking": "food-lover",
        "fitness": "fitness-enthusiast",
        "running": "fitness-enthusiast",
        "music": "music-lover",
        "photography": "visual-creator",
        "travel": "travel-planner",
        "technology": "tech-early-adopter",
        "finance": "finance-planner",
        "gardening": "home-gardener",
    },
    "preferred_category": {
        "food": "food-lover",
        "fitness": "fitness-enthusiast",
        "music": "music-lover",
        "travel": "travel-planner",
        "technology": "tech-early-adopter",
    },
}

INFERRED_CONFIDENCE_MIN = 0.7


def derive_segments(declared: dict[str, str],
                    inferred: dict[str, float]) -> list[str]:
    """Deterministically derive segment membership from attributes."""
    segments: set[str] = set()
    for attr, mapping in SEGMENT_RULES.items():
        value = declared.get(attr)
        if value and value in mapping:
            segments.add(mapping[value])
    for key, confidence in inferred.items():
        if confidence < INFERRED_CONFIDENCE_MIN:
            continue
        for mapping in SEGMENT_RULES.values():
            if key in mapping:
                segments.add(mapping[key])
    return sorted(segments)


class ProfileStore:
    """In-memory versioned profile store with a segment reverse index."""

    def __init__(self) -> None:
        # user_id -> list of versions, newest last
        self._versions: dict[str, list[Profile]] = {}
        # segment -> set of user_ids currently in it
        self.segment_index: dict[str, set[str]] = {}

    # -- creation / update -------------------------------------------------
    def create(self, profile: Profile) -> Profile:
        errors = profile.validate()
        if errors:
            raise ValueError("; ".join(errors))
        if profile.user_id in self._versions:
            raise ValueError(f"profile {profile.user_id} already exists; use update()")
        profile.segments = derive_segments(profile.declared, profile.inferred)
        self._versions[profile.user_id] = [profile]
        self._index(profile)
        return profile

    def update(self, user_id: str, declared: dict[str, str] | None = None,
               inferred: dict[str, float] | None = None,
               now: str = "") -> Profile:
        """Create a new version superseding the current one."""
        current = self.latest(user_id)
        if current is None:
            raise ValueError(f"unknown profile {user_id}")
        if current.status is ProfileStatus.ERASED:
            raise ValueError(f"profile {user_id} is erased and cannot be updated")
        new_declared = dict(current.declared)
        if declared is not None:
            new_declared.update(declared)
        new_inferred = dict(current.inferred)
        if inferred is not None:
            new_inferred.update(inferred)
        new = Profile(user_id=user_id, version=current.version + 1,
                      declared=new_declared, inferred=new_inferred,
                      pii_hash=current.pii_hash,
                      created_at=current.created_at, updated_at=now)
        errors = new.validate()
        if errors:
            raise ValueError("; ".join(errors))
        current.superseded_by = new.version
        new.segments = derive_segments(new.declared, new.inferred)
        self._versions[user_id].append(new)
        self._reindex(user_id)
        return new

    # -- reads --------------------------------------------------------------
    def latest(self, user_id: str) -> Profile | None:
        versions = self._versions.get(user_id)
        return versions[-1] if versions else None

    def version(self, user_id: str, version: int) -> Profile | None:
        for profile in self._versions.get(user_id, []):
            if profile.version == version:
                return profile
        return None

    def history(self, user_id: str) -> list[Profile]:
        return list(self._versions.get(user_id, []))

    def members(self, segment: str) -> set[str]:
        return set(self.segment_index.get(segment, set()))

    def all_user_ids(self) -> list[str]:
        return sorted(self._versions)

    # -- index maintenance --------------------------------------------------
    def _index(self, profile: Profile) -> None:
        for segment in profile.segments:
            self.segment_index.setdefault(segment, set()).add(profile.user_id)

    def _reindex(self, user_id: str) -> None:
        for members in self.segment_index.values():
            members.discard(user_id)
        current = self.latest(user_id)
        if current is not None and current.status is ProfileStatus.ACTIVE:
            self._index(current)

    def remove_from_segments(self, user_id: str) -> None:
        """Drop a user from every derived segment (erasure propagation)."""
        for members in self.segment_index.values():
            members.discard(user_id)
