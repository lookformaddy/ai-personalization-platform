# Consent-Aware Personalization Platform

A **consent-aware personalization platform** for the enterprise: one
place where user context profiles are versioned, ranking is
explainable, consent is enforced fail-closed, experiments are assigned
deterministically, and erasure actually propagates — with a
tamper-evident audit trail behind all of it.

Most personalization systems can tell you what they recommended. Few
can answer, for any given recommendation, "which profile version
produced it, was the purpose consented to, which experiment variant
served it, and was the user later erased?" That gap is what privacy
regulations audit and what trust incidents exploit. This platform
closes it with deterministic, explainable machinery: no black-box
scoring, no network calls, no real model dependency.

## What it does

- **User context profiles** — declared attributes (interests,
  preferred categories) plus inferred attributes with confidence.
  Every update ships as a new version; the old version is superseded,
  never mutated, so a ranking decision is always traceable to the
  exact profile version that produced it. Segments (e.g.
  `food-lover`, `fitness-enthusiast`) are derived deterministically
  from attributes via a rule table, with a reverse index for erasure
  propagation.
- **Feature-aware ranking** — deterministic scoring combining profile
  affinity, item features, recency, and popularity:
  `score = 0.50·affinity + 0.25·popularity + 0.25·recency`, with a
  full per-item score breakdown so every ranking is explainable after
  the fact. Ties break deterministically on item id.
- **Consent & privacy enforcement (fail-closed)** — a purpose-scoped
  consent ledger (`personalization`, `analytics`, `marketing`,
  `notifications`). No ledger entry — or a withdrawn entry — means
  denied. Declared attributes may be used for any consented purpose;
  inferred attributes are personalization-only. PII is stored and
  logged only as a salted SHA-256 hash. Erasure (right to be
  forgotten) tombstones the profile, clears attributes and segments,
  removes the user from every derived segment, anonymizes their
  exposure events, and blocks all future use.
- **Experiment integration** — deterministic SHA-256 hash assignment
  to `control / personalized / personalized+diversity` (no stored
  assignment table to drift). Exposure logging with engagement, and
  per-variant readouts that flag underpowered variants (< 30
  exposures) so nobody draws conclusions from noise.
- **Diversity / fairness guardrails** — per-category exposure caps on
  every ranking (default 2, tightened to 1 in the diversity variant),
  with capped-out items reported and backfill so the caller still gets
  `limit` items. Cold-start profiles (no signal at all) fall back to
  popularity + recency + deterministic exploration jitter, so new
  users still get a useful, reproducible list.
- **Audit trail** — SHA-256 hash-chained, append-only; `verify()`
  replays the chain and reports the first broken link (tamper,
  reorder, or deletion). Ranking decisions and consent changes are
  audited.
- **FastAPI service + CLI**, deterministic synthetic demo (seeded; no
  network calls, no API keys).

## Architecture

See [docs/architecture.md](docs/architecture.md).

```
consent ledger ──▶ usable profile view (purpose-scoped, fail-closed)
                        │
profiles (versioned) ───┼──▶ ranking (affinity + popularity + recency)
                        │         │ explainable breakdowns
                        │         ▼
                        │    diversity caps / cold-start fallback
                        ▼
              experiment assignment (hash) ──▶ exposure log ──▶ readout
                        │
                        ▼
              erasure ──▶ tombstone + segment purge + exposure anonymization
                        │
                        ▼
              hash-chained audit log + personalization report
```

## How to run

```bash
pip install -r requirements.txt
make test          # pytest suite (core tests run on stdlib only)
make demo          # end-to-end demo: profiles → consent → rank → experiment → erase → report
uvicorn apers.service:app --host 0.0.0.0 --port 8000   # with PYTHONPATH=src
```

CLI:

```bash
PYTHONPATH=src python -m apers.cli profile --user-id u1 --interest cooking
PYTHONPATH=src python -m apers.cli consent --user-id u1 --purpose personalization --grant
PYTHONPATH=src python -m apers.cli rank --user-id u1 --limit 5
PYTHONPATH=src python -m apers.cli rank --user-id u1 --no-consent   # shows the fail-closed block
PYTHONPATH=src python -m apers.cli assign --user-id u1 --experiment exp-1
PYTHONPATH=src python -m apers.cli erase --user-id u1
```

## API surface

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| `GET` | `/health` | Liveness + profile/item counts |
| `POST` | `/profiles` | Create a profile (segments derived on write) |
| `GET` | `/profiles/{user_id}` | Profile + consented purposes |
| `POST` | `/profiles/{user_id}` | Versioned update (supersedes vN) |
| `POST` | `/profiles/{user_id}/consent` | Grant / withdraw purpose consent |
| `POST` | `/profiles/{user_id}/erase` | Right-to-be-forgotten tombstone |
| `POST` | `/items`, `GET` | `/items` | Catalog management |
| `POST` | `/rank` | Consent-gated, explainable ranking |
| `GET` | `/experiments/{id}/assign?user_id=` | Deterministic variant assignment |
| `POST` | `/experiments/exposure` | Log an exposure + engagement |
| `GET` | `/experiments/{id}/readout` | Per-variant engagement readout |
| `GET` | `/report` | Consent coverage, exposure, segments, erasures |
| `GET` | `/audit/verify` | Hash-chain verification |

## Key design decisions and trade-offs

- **Deterministic scoring over learned rankers.** A personalization
  decision that affects what a person sees must be explainable to
  them and to an auditor; a weighted sum with a written breakdown is.
  The trade-off is coarseness — scores are ordinal, not learned —
  which is the right trade for a control plane. A learned ranker can
  sit behind the same interface later, with the breakdown contract
  kept.
- **Fail-closed consent everywhere.** No record means denied;
  withdrawal takes effect on the next ranking call; inferred
  attributes are personalization-only even when another purpose is
  consented. A personalization system that fails open manufactures
  false assurance and real regulatory exposure.
- **Profiles versioned, never mutated.** Personalization disputes are
  always about the past ("why did I see this?"). Supersession keeps
  every version readable, so the answer is a lookup, not a guess.
- **Hash assignment, not a lookup table.** Experiment assignment from
  a stable hash means the same user always lands in the same variant
  with no stored state to drift, corrupt, or leak.
- **Pure-stdlib core, FastAPI at the edge.** All decision logic is
  testable without a server, a database, or a network. The service is
  a thin wrapper, so the API can never become a second source of
  truth. The trade-off is in-memory stores: persistence (a real
  database, plus durable audit storage) is the explicit next step.
- **Caps with backfill, not hard truncation.** A diversity cap that
  returned short lists would punish users when a category dominates;
  backfilling from capped-out items keeps list length stable while
  still reporting what the cap removed.

## What it demonstrates

Consent-aware personalization end to end: versioned user context
profiles with derived segments, explainable feature-aware ranking,
purpose-scoped fail-closed consent, PII hashing, right-to-be-
forgotten erasure with propagation, deterministic experiment
assignment and readouts, diversity caps and cold-start fallback,
hash-chained audit, and personalization reporting — built as a typed,
tested, enterprise-shaped Python codebase with a FastAPI service, CLI,
Docker image, and CI definition.
