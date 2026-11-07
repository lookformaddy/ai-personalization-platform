"""Core domain models.

Design notes
------------
* Plain dataclasses, no I/O: the domain logic is testable without a
  database or a network.
* A profile version is the unit of personalization: one user, one
  versioned snapshot of declared + inferred attributes. An update
  ships as a new version and supersedes the old one, so a ranking
  decision can always be traced to the exact profile version that
  produced it.
* Consent is purpose-scoped and fail-closed: no ledger entry (or a
  withdrawn entry) means "not allowed". PII never appears in logs in
  the clear — only its salted hash.
* Timestamps are plain ISO strings; the service layer stamps them. The
  core never calls ``datetime.now`` except through an explicit ``now``
  argument, which keeps tests deterministic.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Purpose(enum.Enum):
    """Purposes personal data may be used for. Consent is granted per
    purpose; a purpose with no granted record is denied (fail-closed)."""

    PERSONALIZATION = "personalization"
    ANALYTICS = "analytics"
    MARKETING = "marketing"
    NOTIFICATIONS = "notifications"


class ProfileStatus(enum.Enum):
    ACTIVE = "active"
    ERASED = "erased"  # right-to-be-forgotten tombstone


class Variant(enum.Enum):
    CONTROL = "control"  # popularity-only ranking, no profile use
    PERSONALIZED = "personalized"  # full feature-aware ranking
    PERSONALIZED_DIVERSITY = "personalized+diversity"  # ranking + strict caps


@dataclass
class Profile:
    """One versioned user context profile.

    ``declared`` attributes are supplied by the user (e.g. interests,
    preferred categories). ``inferred`` attributes are derived by the
    platform with a confidence in 0..1. ``segments`` are derived from
    both via :mod:`apers.profiles` rules. ``pii_hash`` is the salted
    hash of the identifying contact value (e.g. email); the raw value
    is never stored in this platform.
    """

    user_id: str
    version: int = 1
    declared: dict[str, str] = field(default_factory=dict)
    inferred: dict[str, float] = field(default_factory=dict)
    segments: list[str] = field(default_factory=list)
    pii_hash: str = ""
    status: ProfileStatus = ProfileStatus.ACTIVE
    superseded_by: int | None = None
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.user_id.strip():
            errors.append("user_id is required")
        if self.version < 1:
            errors.append("version must be >= 1")
        for key, value in self.inferred.items():
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                errors.append(f"inferred[{key!r}] confidence must be in 0..1")
        if self.status is ProfileStatus.ERASED and (self.declared or self.inferred or self.segments):
            errors.append("erased profile must carry no attributes or segments")
        return errors

    @property
    def is_cold_start(self) -> bool:
        """True when the profile carries no usable signal: ranking must
        fall back to popularity + exploration."""
        return not self.declared and not self.inferred and not self.segments


@dataclass
class Item:
    """One rankable content item. Features are generic topic weights
    in 0..1 (e.g. ``{"cooking": 0.9, "budget": 0.4}``)."""

    item_id: str
    title: str
    category: str
    features: dict[str, float] = field(default_factory=dict)
    popularity: float = 0.0  # 0..1, e.g. normalized engagement rate
    published_at: str = ""  # ISO date, e.g. "2026-11-01"
    tags: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.item_id.strip():
            errors.append("item_id is required")
        if not self.category.strip():
            errors.append("category is required")
        if not 0.0 <= self.popularity <= 1.0:
            errors.append("popularity must be in 0..1")
        for key, value in self.features.items():
            if not 0.0 <= value <= 1.0:
                errors.append(f"features[{key!r}] must be in 0..1")
        return errors


@dataclass
class ScoredItem:
    """One item with its deterministic score and full breakdown, so a
    ranking decision is explainable after the fact."""

    item_id: str
    score: float
    breakdown: dict[str, float]  # component -> contribution (sums to score)
    rank: int = 0
    cold_start: bool = False


@dataclass
class ConsentRecord:
    """One purpose-scoped consent decision. The ledger is append-only
    in spirit: a withdrawal is a new record, never an edit."""

    user_id: str
    purpose: Purpose
    granted: bool
    recorded_at: str = ""
    source: str = ""  # e.g. "settings-page", "onboarding", "api"


@dataclass
class ExposureEvent:
    """One logged exposure of a ranked item under an experiment
    variant, with whether the user engaged (click / watch / save)."""

    user_id: str
    experiment_id: str
    variant: Variant
    item_id: str
    engaged: bool
    recorded_at: str = ""


@dataclass
class RankingDecision:
    """The auditable record of one ranking call: who, what purpose,
    which profile version, which variant, and what came out."""

    user_id: str
    profile_version: int
    purpose: Purpose
    variant: Variant
    item_ids: list[str]
    cold_start: bool
    decided_at: str = ""


@dataclass
class AuditEntry:
    """One hash-chained audit record."""

    seq: int
    timestamp: str
    actor: str
    action: str
    subject_id: str
    detail: str
    prev_hash: str
    entry_hash: str
