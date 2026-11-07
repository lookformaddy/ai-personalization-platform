"""FastAPI surface for the consent-aware personalization platform.

The service is a thin HTTP wrapper over the pure-stdlib core: it owns
the in-memory stores, stamps timestamps, and translates domain errors
into HTTP responses (PermissionError -> 403, ValueError -> 400,
unknown profile/item -> 404, duplicate -> 409). All decision logic
lives in the core modules, so the API adds no second source of truth.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from apers.audit import AuditLog
from apers.consent import ConsentLedger, hash_pii, usable_profile
from apers.erasure import erase_profile
from apers.experiments import ExperimentLog, assign_variant
from apers.models import (
    ExposureEvent,
    Item,
    Profile,
    ProfileStatus,
    Purpose,
    Variant,
)
from apers.profiles import ProfileStore
from apers.ranking import rank
from apers.reporting import personalization_report

PII_SALT = os.environ.get("APERS_PII_SALT", "apers-pii-salt")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


app = FastAPI(title="Consent-Aware Personalization Platform", version="1.0.0")

# In-memory stores (a real deployment backs these with a database).
profiles = ProfileStore()
consent = ConsentLedger()
audit = AuditLog()
experiments = ExperimentLog()
decisions: list = []
items: dict[str, Item] = {}


class CreateProfileRequest(BaseModel):
    user_id: str
    declared: dict[str, str] = {}
    inferred: dict[str, float] = {}
    email: str = ""  # stored only as its salted hash


class UpdateProfileRequest(BaseModel):
    declared: dict[str, str] | None = None
    inferred: dict[str, float] | None = None


class ConsentRequest(BaseModel):
    purpose: Purpose
    granted: bool
    source: str = "api"


class ItemRequest(BaseModel):
    item_id: str
    title: str
    category: str
    features: dict[str, float] = {}
    popularity: float = 0.0
    published_at: str = ""
    tags: list[str] = []


class RankRequest(BaseModel):
    user_id: str
    purpose: Purpose = Purpose.PERSONALIZATION
    limit: int = 10
    variant: Variant = Variant.PERSONALIZED
    max_per_category: int = 2
    today: str = ""


class ExposureRequest(BaseModel):
    user_id: str
    experiment_id: str
    variant: Variant
    item_id: str
    engaged: bool


def _profile_or_404(user_id: str) -> Profile:
    profile = profiles.latest(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown profile {user_id}")
    return profile


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "profiles": len(profiles.all_user_ids()),
            "items": len(items)}


@app.post("/profiles", status_code=201)
def create_profile(req: CreateProfileRequest) -> dict:
    profile = Profile(user_id=req.user_id, declared=req.declared,
                      inferred=req.inferred,
                      pii_hash=hash_pii(req.email, PII_SALT) if req.email else "",
                      created_at=_now(), updated_at=_now())
    try:
        profiles.create(profile)
    except ValueError as exc:
        raise HTTPException(status_code=409 if "already exists" in str(exc) else 400,
                            detail=str(exc)) from exc
    audit.append(_now(), "api", "profile_created", req.user_id,
                 f"v{profile.version} segments={','.join(profile.segments)}")
    return {"user_id": profile.user_id, "version": profile.version,
            "segments": profile.segments, "pii_hash": profile.pii_hash}


@app.get("/profiles/{user_id}")
def get_profile(user_id: str) -> dict:
    profile = _profile_or_404(user_id)
    return {"user_id": profile.user_id, "version": profile.version,
            "declared": profile.declared, "inferred": profile.inferred,
            "segments": profile.segments, "status": profile.status.value,
            "superseded_by": profile.superseded_by,
            "consented_purposes": [p.value for p in consent.granted_purposes(user_id)]}


@app.post("/profiles/{user_id}")
def update_profile(user_id: str, req: UpdateProfileRequest) -> dict:
    try:
        updated = profiles.update(user_id, declared=req.declared,
                                  inferred=req.inferred, now=_now())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.append(_now(), "api", "profile_updated", user_id, f"v{updated.version}")
    return {"user_id": user_id, "version": updated.version,
            "segments": updated.segments}


@app.post("/profiles/{user_id}/consent", status_code=201)
def set_consent(user_id: str, req: ConsentRequest) -> dict:
    _profile_or_404(user_id)
    record = (consent.grant if req.granted else consent.withdraw)(
        user_id, req.purpose, now=_now(), source=req.source)
    audit.append(_now(), "api", f"consent_{'granted' if req.granted else 'withdrawn'}",
                 user_id, req.purpose.value)
    return {"user_id": user_id, "purpose": record.purpose.value,
            "granted": record.granted}


@app.post("/profiles/{user_id}/erase")
def erase(user_id: str) -> dict:
    try:
        tombstone = erase_profile(profiles, user_id, experiments, now=_now())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.append(_now(), "api", "profile_erased", user_id,
                 f"tombstone v{tombstone.version}")
    return {"user_id": user_id, "status": tombstone.status.value,
            "version": tombstone.version}


@app.post("/items", status_code=201)
def add_item(req: ItemRequest) -> dict:
    if req.item_id in items:
        raise HTTPException(status_code=409, detail=f"item {req.item_id} already exists")
    item = Item(**req.model_dump())
    errors = item.validate()
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors)) from None
    items[item.item_id] = item
    return {"item_id": item.item_id, "category": item.category}


@app.get("/items")
def list_items() -> dict:
    return {"items": sorted(items)}


@app.post("/rank")
def rank_items(req: RankRequest) -> dict:
    profile = _profile_or_404(req.user_id)
    if profile.status is ProfileStatus.ERASED:
        raise HTTPException(status_code=403, detail=f"profile {req.user_id} is erased")
    try:
        usable = usable_profile(profile, req.purpose, consent)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    ranked, capped_out, decision = rank(
        usable, list(items.values()), today=req.today or _now()[:10],
        limit=req.limit, variant=req.variant,
        max_per_category=req.max_per_category)
    decisions.append(decision)
    audit.append(_now(), "api", "ranking_decided", req.user_id,
                 f"v{decision.profile_version} variant={req.variant.value} "
                 f"items={len(ranked)} cold_start={decision.cold_start}")
    return {"user_id": req.user_id, "profile_version": decision.profile_version,
            "variant": req.variant.value, "cold_start": decision.cold_start,
            "ranked": [{"item_id": s.item_id, "score": s.score,
                        "breakdown": s.breakdown, "rank": s.rank} for s in ranked],
            "capped_out": capped_out}


@app.get("/experiments/{experiment_id}/assign")
def assign(experiment_id: str, user_id: str) -> dict:
    _profile_or_404(user_id)
    return {"experiment_id": experiment_id, "user_id": user_id,
            "variant": assign_variant(experiment_id, user_id).value}


@app.post("/experiments/exposure", status_code=201)
def log_exposure(req: ExposureRequest) -> dict:
    event = ExposureEvent(user_id=req.user_id, experiment_id=req.experiment_id,
                          variant=req.variant, item_id=req.item_id,
                          engaged=req.engaged, recorded_at=_now())
    experiments.log_exposure(event)
    return {"experiment_id": req.experiment_id, "variant": req.variant.value,
            "engaged": req.engaged}


@app.get("/experiments/{experiment_id}/readout")
def readout(experiment_id: str) -> dict:
    return experiments.readout(experiment_id)


@app.get("/report")
def report(experiment_id: str = "") -> dict:
    return personalization_report(profiles, consent, decisions, items,
                                  experiments, experiment_id)


@app.get("/audit/verify")
def audit_verify() -> dict:
    ok, message = audit.verify()
    return {"valid": ok, "message": message, "entries": len(audit.entries)}
