"""Purpose-scoped consent ledger and PII hashing.

Fail-closed by design: a purpose is usable only when the ledger holds
an explicit, still-granted record for (user, purpose). No record, or a
withdrawn record, means denied. Declared attributes may be used for
any consented purpose; inferred attributes are personalization-only —
they may be used solely when PERSONALIZATION consent is granted.
PII is never logged or stored in the clear: only its salted SHA-256
hash leaves the consent boundary.
"""
from __future__ import annotations

import hashlib

from apers.models import ConsentRecord, Profile, Purpose

# Inferred attributes are the most sensitive derived signal: they may
# only feed personalization, never analytics/marketing/notifications.
INFERRED_REQUIRES = Purpose.PERSONALIZATION


def hash_pii(value: str, salt: str = "apers-pii-salt") -> str:
    """Salted SHA-256 of a PII value (e.g. email). The raw value never
    leaves the caller; only this digest is stored or logged."""
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


class ConsentLedger:
    """Append-only (in spirit) purpose-scoped consent records.

    ``grant``/``withdraw`` append a new record; ``is_allowed`` reads
    the latest record for (user, purpose). With no record at all the
    answer is False — fail-closed.
    """

    def __init__(self) -> None:
        self._records: list[ConsentRecord] = []

    def grant(self, user_id: str, purpose: Purpose, now: str = "",
              source: str = "") -> ConsentRecord:
        record = ConsentRecord(user_id=user_id, purpose=purpose, granted=True,
                               recorded_at=now, source=source)
        self._records.append(record)
        return record

    def withdraw(self, user_id: str, purpose: Purpose, now: str = "",
                 source: str = "") -> ConsentRecord:
        record = ConsentRecord(user_id=user_id, purpose=purpose, granted=False,
                               recorded_at=now, source=source)
        self._records.append(record)
        return record

    def is_allowed(self, user_id: str, purpose: Purpose) -> bool:
        """Latest record wins; no record means denied (fail-closed)."""
        for record in reversed(self._records):
            if record.user_id == user_id and record.purpose is purpose:
                return record.granted
        return False

    def granted_purposes(self, user_id: str) -> list[Purpose]:
        return [p for p in Purpose if self.is_allowed(user_id, p)]

    def history(self, user_id: str) -> list[ConsentRecord]:
        return [r for r in self._records if r.user_id == user_id]


def usable_profile(profile: Profile, purpose: Purpose,
                   ledger: ConsentLedger) -> Profile:
    """Return the view of a profile usable for ``purpose``.

    Raises ``PermissionError`` when the purpose is not consented
    (fail-closed). Declared attributes and segments are usable for any
    consented purpose; inferred attributes only for PERSONALIZATION.
    Erased profiles are never usable.
    """
    if profile.status.value == "erased":
        raise PermissionError(f"profile {profile.user_id} is erased")
    if not ledger.is_allowed(profile.user_id, purpose):
        raise PermissionError(
            f"no consent for purpose={purpose.value} for user {profile.user_id}")
    inferred = profile.inferred if purpose is INFERRED_REQUIRES else {}
    return Profile(user_id=profile.user_id, version=profile.version,
                   declared=dict(profile.declared), inferred=dict(inferred),
                   segments=list(profile.segments), pii_hash=profile.pii_hash,
                   status=profile.status, created_at=profile.created_at,
                   updated_at=profile.updated_at)
