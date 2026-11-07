"""Right-to-be-forgotten erasure: tombstone a profile and propagate
the erasure to derived state.

Erasure is fail-closed and irreversible:
* the profile's declared + inferred attributes and segments are
  cleared, and its status becomes ERASED (a tombstone keeps only the
  user id, version, and a hashed PII reference so later calls can
  still prove the profile existed and was erased);
* the user is removed from every derived segment (via the store's
  reverse index);
* exposure events for the user are anonymized — the user id is
  replaced with the tombstone hash — so experiment readouts keep
  their counts without retaining an identifier;
* the profile can never be updated or ranked again; any attempt
  raises.
"""
from __future__ import annotations

from apers.consent import hash_pii
from apers.experiments import ExperimentLog
from apers.models import Profile, ProfileStatus
from apers.profiles import ProfileStore


def erase_profile(store: ProfileStore, user_id: str,
                  experiment_log: ExperimentLog | None = None,
                  now: str = "") -> Profile:
    """Tombstone the latest profile version for ``user_id`` and
    propagate erasure to segments and exposure events."""
    profile = store.latest(user_id)
    if profile is None:
        raise ValueError(f"unknown profile {user_id}")
    if profile.status is ProfileStatus.ERASED:
        raise ValueError(f"profile {user_id} is already erased")
    tombstone = Profile(
        user_id=user_id, version=profile.version + 1,
        declared={}, inferred={}, segments=[],
        pii_hash=hash_pii(profile.pii_hash or user_id),
        status=ProfileStatus.ERASED,
        created_at=profile.created_at, updated_at=now)
    profile.superseded_by = tombstone.version
    store._versions[user_id].append(tombstone)
    store.remove_from_segments(user_id)
    if experiment_log is not None:
        for event in experiment_log.events:
            if event.user_id == user_id:
                event.user_id = f"erased:{tombstone.pii_hash[:12]}"
    return tombstone
