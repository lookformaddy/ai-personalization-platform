# Architecture — Consent-Aware Personalization Platform

## Diagram (described)

```
                        ┌─────────────────────┐
                        │  FastAPI service     │
                        │ (apers/service.py)   │
                        │  thin HTTP wrapper   │
                        └──────┬──────────────┘
                               │ owns stores, stamps time,
                               │ maps PermissionError -> 403
        ┌──────────────┬───────┼───────────────┬──────────────┐
        ▼              ▼       ▼               ▼              ▼
  ┌───────────┐ ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ profiles  │ │ consent   │ │ ranking  │ │experiments│ │ erasure │
  │ .py       │ │ .py       │ │ .py      │ │ .py       │ │ .py     │
  │ versions  │ │ ledger +  │ │ scoring, │ │ hash      │ │ tomb-   │
  │ + segments│ │ PII hash  │ │ caps,    │ │ assign,   │ │ stone + │
  └───────────┘ └───────────┘ │ cold-    │ │ readout   │ │ purge   │
        │              │      │ start    │ └──────────┘ └──────────┘
        ▼              ▼      └──────────┘       │              │
  ┌────────────────────────────────────────────────────────────────┐
  │  models.py — dataclasses (profiles, items, scored items,      │
  │  consent records, exposure events, ranking decisions, audit    │
  │  entries). No I/O, no decisions.                              │
  └────────────────────────────────────────────────────────────────┘
        │              │           │            │              │
        ▼              ▼           ▼            ▼              ▼
  ┌──────────────┐ ┌───────────┐ ┌───────────────────────────────┐
  │ audit.py     │ │reporting.py│ │  cli.py / scripts/demo.py     │
  │ hash-chained │ │ coverage / │ │  entry points over the core   │
  │ log          │ │ exposure   │ │                               │
  └──────────────┘ └───────────┘ └───────────────────────────────┘
```

All decision logic lives in the pure-stdlib core (`profiles`,
`consent`, `ranking`, `experiments`, `erasure`, `audit`,
`reporting`). The FastAPI service and the CLI are entry points only:
they own the in-memory stores, stamp timestamps, and translate domain
errors into HTTP responses. There is exactly one implementation of
every rule, so the API can never drift from the core.

## Module responsibilities

| Module | Responsibility |
| ------ | -------------- |
| `models.py` | Domain dataclasses and enums. `Purpose` scopes consent; `Profile` carries its own cold-start test and validation so no other module hard-codes those rules. No I/O; timestamps are explicit arguments, which keeps tests deterministic. |
| `profiles.py` | Versioned profile store: create / update-with-supersession, deterministic segment derivation from declared + inferred attributes, and the segment reverse index used by erasure. |
| `consent.py` | Purpose-scoped ledger (latest record wins; no record = denied), the usable-profile view that strips inferred attributes for non-personalization purposes, and salted PII hashing. |
| `ranking.py` | Deterministic weighted scoring with full breakdowns, per-category caps with backfill, control-variant (profile-blind) weights, cold-start weights, and deterministic exploration jitter. |
| `experiments.py` | Stable-hash variant assignment (`control / personalized / personalized+diversity`), exposure logging, and per-variant engagement readouts with an underpowered-sample flag. |
| `erasure.py` | Right-to-be-forgotten: tombstone the latest version, purge derived segments via the reverse index, anonymize exposure events, and make the profile unusable thereafter. |
| `audit.py` | Append-only SHA-256 hash-chained log; `verify()` replays the chain and reports the first broken link. |
| `reporting.py` | Personalization report: consent coverage, cold-start count, per-category exposure shares, segment sizes, erasure counts, and an optional experiment readout — all computed from the live stores. |
| `service.py` | FastAPI app; in-memory stores; request validation via Pydantic; domain `PermissionError` → 403, `ValueError` → 400, unknown profile/item → 404, duplicate → 409. |
| `cli.py` | `profile`, `consent`, `rank`, `assign`, `erase`, and `report` commands over the core with a small synthetic catalog. |

## Data flow: ranking a consented user

1. **Create profile** — `POST /profiles` validates, derives segments,
   and stores the PII hash (never the raw value).
2. **Grant consent** — `POST /profiles/{id}/consent` appends a
   purpose-scoped record; the ledger is append-only in spirit.
3. **Rank** — `POST /rank` builds the purpose-scoped usable profile
   view (fail-closed if unconsented), scores items with the variant's
   weights, applies the category cap with backfill, and records a
   `RankingDecision` naming the exact profile version used.
4. **Experiment** — `GET /experiments/{id}/assign` hashes
   (experiment, user) into a variant; exposures + engagement are
   logged; the readout reports per-variant rates.
5. **Erase** — `POST /profiles/{id}/erase` tombstones the profile,
   purges segments, anonymizes exposures, and blocks future ranking.
6. **Report** — `/report` aggregates consent coverage, exposure, and
   erasures; `/audit/verify` proves the chain.

## Invariants

- A purpose with no granted consent record is denied, never allowed.
- Inferred attributes are usable only for personalization, even when
  another purpose is consented.
- A ranking decision always names the profile version that produced it.
- A profile update supersedes the previous version; history is never
  mutated.
- An erased profile can never be updated or ranked again.
- Variant assignment is a pure function of (experiment, user): the
  same user always lands in the same variant.
- The audit chain is append-only; any edit, reorder, or deletion is
  detected by `verify()`.

## Trade-offs and next steps

- **In-memory stores.** The service keeps state in process memory so
  the core stays dependency-free and testable. A production deployment
  backs the stores with a database and persists the audit log to
  write-once storage.
- **Salted hashing, not encryption.** PII hashing is one-way by
  design, which is what an audit log needs; a production system would
  keep the raw contact value in a separate, access-controlled identity
  store and never in the personalization service at all.
- **Synchronous ranking.** Ranking runs inline in the request. A
  production catalog of millions of items would pre-filter candidates
  (e.g. by segment) before scoring and cache per-profile rankings
  with invalidation on profile updates.
- **No authentication/authorization.** The service assumes a trusted
  network; identity and role checks (who may erase which profile)
  belong in front of it.
