"""Hash-chained audit log and the personalization report."""
from apers.audit import AuditLog
from apers.consent import ConsentLedger
from apers.models import Item, Profile, Purpose
from apers.profiles import ProfileStore
from apers.ranking import rank
from apers.reporting import personalization_report


def _setup():
    store = ProfileStore()
    ledger = ConsentLedger()
    store.create(Profile(user_id="u1", declared={"interest": "cooking"}))
    store.create(Profile(user_id="u2"))
    ledger.grant("u1", Purpose.PERSONALIZATION, now="2026-11-07")
    items = [Item("it-01", "Pasta", "food", {"cooking": 0.9}, 0.8, "2026-11-05"),
             Item("it-02", "Lisbon", "travel", {"travel": 0.9}, 0.7, "2026-11-01")]
    _, _, decision = rank(store.latest("u1"), items, "2026-11-07", limit=2)
    return store, ledger, [decision], {it.item_id: it for it in items}


def test_audit_chain_verifies_and_detects_tampering():
    audit = AuditLog()
    audit.append("2026-11-07", "demo", "profile_created", "u1", "v1")
    audit.append("2026-11-07", "demo", "ranking_decided", "u1", "top=it-01")
    ok, message = audit.verify()
    assert ok is True and "2 entries" in message
    assert len(audit.for_subject("u1")) == 2
    # Tamper with the second entry's detail: verification must fail.
    audit.entries[1].detail = "top=it-02"
    ok, message = audit.verify()
    assert ok is False and "hash mismatch" in message


def test_audit_detects_reorder():
    audit = AuditLog()
    audit.append("2026-11-07", "demo", "a", "u1")
    audit.append("2026-11-07", "demo", "b", "u1")
    audit.entries[0], audit.entries[1] = audit.entries[1], audit.entries[0]
    ok, _ = audit.verify()
    assert ok is False


def test_report_counts_consent_coverage_and_exposure():
    store, ledger, decisions, items = _setup()
    report = personalization_report(store, ledger, decisions, items)
    assert report["profiles_total"] == 2
    assert report["profiles_active"] == 2
    assert report["consent_personalization"] == 1
    assert report["consent_coverage_pct"] == 50.0
    assert report["cold_start_profiles"] == 1  # u2 has no signal
    assert report["category_exposure"] == {"food": 1, "travel": 1}
    assert report["category_exposure_share"] == {"food": 0.5, "travel": 0.5}
    assert report["segments"] == {"food-lover": 1}
    assert report["ranking_decisions"] == 1


def test_report_empty_state():
    report = personalization_report(ProfileStore(), ConsentLedger(), [], {})
    assert report["profiles_total"] == 0
    assert report["consent_coverage_pct"] == 0.0
    assert report["category_exposure"] == {}
