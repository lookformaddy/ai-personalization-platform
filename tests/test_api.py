"""API tests. Auto-skips when FastAPI is not installed (runs in CI)."""
import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI not installed")
from fastapi.testclient import TestClient  # noqa: E402

from apers.service import app  # noqa: E402


def _client():
    return TestClient(app)


def test_health():
    body = _client().get("/health").json()
    assert body["status"] == "ok"


def test_create_profile_assigns_segments_and_hashes_pii():
    client = _client()
    resp = client.post("/profiles", json={
        "user_id": "api-u1", "declared": {"interest": "cooking"},
        "email": "u1@example.com"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["segments"] == ["food-lover"]
    assert body["pii_hash"] and "u1@example.com" not in body["pii_hash"]
    assert client.post("/profiles", json={"user_id": "api-u1"}).status_code == 409


def test_rank_requires_personalization_consent():
    client = _client()
    client.post("/profiles", json={"user_id": "api-u2",
                                   "declared": {"interest": "cooking"}})
    client.post("/items", json={"item_id": "api-it-1", "title": "Pasta",
                                "category": "food", "features": {"cooking": 0.9},
                                "popularity": 0.8, "published_at": "2026-11-05"})
    # No consent yet: fail-closed 403.
    assert client.post("/rank", json={"user_id": "api-u2"}).status_code == 403
    # Grant consent: ranking works and explains itself.
    assert client.post("/profiles/api-u2/consent",
                       json={"purpose": "personalization", "granted": True}
                       ).status_code == 201
    ranked = client.post("/rank", json={"user_id": "api-u2", "limit": 1}).json()
    assert ranked["ranked"][0]["item_id"] == "api-it-1"
    assert ranked["ranked"][0]["breakdown"]["affinity"] > 0


def test_consent_withdrawal_blocks_ranking():
    client = _client()
    client.post("/profiles", json={"user_id": "api-u3"})
    client.post("/profiles/api-u3/consent",
                json={"purpose": "personalization", "granted": True})
    client.post("/profiles/api-u3/consent",
                json={"purpose": "personalization", "granted": False})
    assert client.post("/rank", json={"user_id": "api-u3"}).status_code == 403


def test_full_flow_via_api():
    client = _client()
    uid = "api-flow"
    client.post("/profiles", json={"user_id": uid,
                                   "declared": {"interest": "cooking"},
                                   "inferred": {"baking": 0.85}})
    client.post("/profiles/api-flow/consent".replace("api-flow", uid),
                json={"purpose": "personalization", "granted": True})
    for item in (
        {"item_id": "f-it-1", "title": "Pasta", "category": "food",
         "features": {"cooking": 0.9}, "popularity": 0.8, "published_at": "2026-11-05"},
        {"item_id": "f-it-2", "title": "Sourdough", "category": "food",
         "features": {"baking": 0.9}, "popularity": 0.6, "published_at": "2026-10-20"},
        {"item_id": "f-it-3", "title": "5k plan", "category": "fitness",
         "features": {"running": 0.9}, "popularity": 0.7, "published_at": "2026-11-02"},
    ):
        assert client.post("/items", json=item).status_code == 201
    # Experiment assignment is deterministic.
    v1 = client.get(f"/experiments/exp-1/assign?user_id={uid}").json()["variant"]
    v2 = client.get(f"/experiments/exp-1/assign?user_id={uid}").json()["variant"]
    assert v1 == v2
    # Rank with the assigned variant and log an exposure.
    ranked = client.post("/rank", json={"user_id": uid, "limit": 2, "variant": v1}).json()
    assert len(ranked["ranked"]) == 2
    assert client.post("/experiments/exposure", json={
        "user_id": uid, "experiment_id": "exp-1", "variant": v1,
        "item_id": ranked["ranked"][0]["item_id"], "engaged": True}).status_code == 201
    readout = client.get("/experiments/exp-1/readout").json()
    assert readout["total_exposures"] >= 1
    # Erase: profile tombstoned, ranking blocked afterwards.
    erased = client.post(f"/profiles/{uid}/erase").json()
    assert erased["status"] == "erased"
    assert client.post("/rank", json={"user_id": uid}).status_code == 403
    # Report + audit still verify.
    report = client.get("/report").json()
    assert report["profiles_erased"] >= 1
    verify = client.get("/audit/verify").json()
    assert verify["valid"] is True
    assert verify["entries"] >= 1
