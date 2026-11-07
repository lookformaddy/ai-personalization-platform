"""Consent ledger: fail-closed purpose checks, PII hashing, and the
usable-profile view."""
import pytest

from apers.consent import ConsentLedger, hash_pii, usable_profile
from apers.models import Profile, Purpose


def _profile() -> Profile:
    return Profile(user_id="u1", declared={"interest": "cooking"},
                   inferred={"baking": 0.9}, segments=["food-lover"])


def test_no_record_means_denied_fail_closed():
    ledger = ConsentLedger()
    assert ledger.is_allowed("u1", Purpose.PERSONALIZATION) is False
    assert ledger.granted_purposes("u1") == []


def test_grant_then_withdraw_latest_wins():
    ledger = ConsentLedger()
    ledger.grant("u1", Purpose.PERSONALIZATION, now="2026-11-07")
    assert ledger.is_allowed("u1", Purpose.PERSONALIZATION) is True
    ledger.withdraw("u1", Purpose.PERSONALIZATION, now="2026-11-08")
    assert ledger.is_allowed("u1", Purpose.PERSONALIZATION) is False
    assert len(ledger.history("u1")) == 2  # append-only history kept


def test_consent_is_per_purpose():
    ledger = ConsentLedger()
    ledger.grant("u1", Purpose.ANALYTICS)
    assert ledger.is_allowed("u1", Purpose.ANALYTICS) is True
    assert ledger.is_allowed("u1", Purpose.PERSONALIZATION) is False


def test_usable_profile_personalization_keeps_inferred():
    ledger = ConsentLedger()
    ledger.grant("u1", Purpose.PERSONALIZATION)
    usable = usable_profile(_profile(), Purpose.PERSONALIZATION, ledger)
    assert usable.inferred == {"baking": 0.9}
    assert usable.segments == ["food-lover"]


def test_usable_profile_analytics_strips_inferred():
    ledger = ConsentLedger()
    ledger.grant("u1", Purpose.ANALYTICS)
    usable = usable_profile(_profile(), Purpose.ANALYTICS, ledger)
    assert usable.inferred == {}  # inferred is personalization-only
    assert usable.declared == {"interest": "cooking"}


def test_usable_profile_without_consent_raises():
    ledger = ConsentLedger()
    with pytest.raises(PermissionError, match="no consent"):
        usable_profile(_profile(), Purpose.PERSONALIZATION, ledger)


def test_hash_pii_is_salted_and_stable():
    h1 = hash_pii("a@example.com")
    h2 = hash_pii("a@example.com")
    assert h1 == h2 and len(h1) == 64 and "a@example.com" not in h1
    assert hash_pii("a@example.com") != hash_pii("b@example.com")
    assert hash_pii("a@example.com", salt="other") != h1
