# Component Architecture

## 1. Purpose

Zoom in from the system-level diagram (`system-architecture.md`) to the
package/module level within `backend/`, `ml/`, and `simulation/`, so
implementation has a concrete map to follow.

## 2. Context

Phase 0 creates the folder skeletons; this document defines what belongs in
each folder and the dependency rules between them, before real code lands.

## 3. Current decision

### `backend/`

```
backend/
├── api/                 FastAPI routers (HTTP boundary only — no business logic)
├── models/              SQLAlchemy (or equivalent) ORM models mirroring data/database-schema.md
├── schemas/             Pydantic request/response schemas
├── services/            Application services: event normalisation, recovery-eligibility gate, feature pipeline, LLM service, notification service
├── workers/             Celery/Redis Streams task definitions (async jobs: training, batch scoring)
├── policies/            Deterministic policy ENGINE (how rules are evaluated). Policy DATA
│                        (what is allowed) is versioned, immutable-per-version merchant config
│                        in the DB — see data/data-model.md. No executable per-merchant policy code.
├── decision_engine/     Incremental value engine + action optimizer (recommendation) + DecisionRecord writer (calls ml/inference, calls policies)
├── integrations/        Razorpay client + webhook verification; message gateway (simulated for MVP) with WhatsApp/SMS/Email clients + Sarvam voice client (stubbed until Phase 10)
├── database/            DB session/engine setup, migrations
└── core/                Config, logging, security/secrets loading, shared constants
```

Dependency rule: `api → services/decision_engine → policies → models/schemas`.
`decision_engine` may depend on `ml` (via a thin inference interface) and on
`policies`. `policies` must never depend on `decision_engine` or `ml` — it
must be evaluable as a pure function of (proposed action, context, rules).

### `ml/`

```
ml/
├── data/          Data loading/access utilities (reads simulation output or real data)
├── features/      Feature engineering — shared between training and inference
├── models/        Model definitions; training output artifacts + immutable ModelVersion
│                  records here (status DRAFT→VALIDATED→PROMOTED→RETIRED, or →REJECTED;
│                  one PROMOTED per model role — see data/data-model.md, ADR-011)
├── training/      Training pipeline entrypoints
├── evaluation/    Metrics, calibration, uplift/Qini evaluation
├── inference/      Thin, stable interface the backend calls (predict_baseline, predict_action)
└── experiments/   Notebooks/scripts for exploratory work — never imported by backend
```

Dependency rule: `backend/decision_engine` imports only from `ml/inference`,
never directly from `ml/training` or `ml/experiments`. This keeps the
backend decoupled from how models are produced.

### `simulation/`

```
simulation/
├── generator/       Merchant/customer/payment/intervention-effect generators
├── scenarios/       Named scenario configs (e.g. "3 contrasting merchants")
├── ground_truth/    Hidden true probabilities/effects — never fed to models, only to evaluation
└── evaluation/      Compares model predictions/decisions against ground truth
```

## 4. Alternatives considered

Considered collapsing `decision_engine` into `services/` as just another
service. Rejected — the decision engine is the single most important, most
scrutinized piece of this product (per the core principle "financial
decisions must remain controlled by deterministic logic + ML + policy
constraints"); giving it its own top-level package makes that boundary
visible in the repo structure itself, not just in prose.

## 5. Why this option

Matches the instruction-mandated top-level layout exactly, while adding just
enough internal structure (dependency rules) to make the "policy engine can
always veto" and "ML never talks to Razorpay directly" guarantees checkable
by looking at import statements, not just by trusting documentation.

## 6. Example

```python
# ALLOWED
from backend.decision_engine.optimizer import select_action
from backend.policies.engine import check_policy
from ml.inference.recovery_model import predict_action_probabilities

# NOT ALLOWED (violates dependency rule)
from backend.policies.engine import ...   # inside backend/decision_engine is fine
from backend.decision_engine import ...   # inside backend/policies is NOT fine
```

## 7. Implementation implications

- A lightweight import-linter or simple test (`tests/backend/test_dependency_rules.py`,
  to be added in Phase 6) should assert `policies/` has zero imports from
  `decision_engine/` or `ml/`.
- `ml/inference` must expose a stable, small function signature so that
  swapping logistic regression for LightGBM (per `ml/models.md` roadmap)
  never requires touching `backend/decision_engine`.

## 8. Open questions

- Whether `services/` needs its own `llm/` subpackage once Phase 9 begins,
  separate from other services — likely yes, to keep the privacy-filter
  boundary (see `architecture/privacy-architecture.md`) in one obvious
  place.

## 9. Visual

```
        api/  ──────────────┐
                             ▼
        services/ ──► decision_engine/ ──► policies/
                             │                 │
                             ▼                 ▼
                        ml/inference/     models/schemas/
```
