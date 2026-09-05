# System Architecture

## 1. Purpose

Describe the full system shape: components, how they connect, and why this
particular set of technologies and no more.

## 2. Context

The architecture must support the core loop (events → data → ML prediction
→ incremental value → optimization → policy → controlled action → measured
outcome → learning) end-to-end, while staying buildable by a small team in
hackathon time. Every technology choice below was evaluated against "does
this make the product better/more provably correct, or does it just look
impressive on a diagram."

## 3. Current decision — approved architecture

```
                         RAZORPAY EVENTS
                               │
                               ▼
                      ┌─────────────────┐
                      │ EVENT INGESTION │   (webhook receiver, Phase 8;
                      └────────┬────────┘    synthetic feed until then;
                               │             normalises to the 5-value
                               ▼             PaymentEvent vocabulary)
                      ┌─────────────────┐
                      │ RECOVERY        │   (a gate: PAYMENT_FAILED does NOT
                      │ ELIGIBILITY     │    auto-create a case — see
                      └────────┬────────┘    data/data-model.md)
                          eligible │  (ineligible → no case, logged)
                               ▼
                      ┌─────────────────┐
                      │ FEATURE PIPELINE│
                      └────────┬────────┘
                               │
                               ▼
                      ┌─────────────────┐
                      │  FEATURE STORE  │   (Postgres tables, not a
                      └────────┬────────┘    dedicated feature-store product)
                               │
             ┌─────────────────┼──────────────────┐
             │                 │                  │
             ▼                 ▼                  ▼
      RECOVERY MODEL     BASELINE MODEL    CUSTOMER/MERCHANT
      (per-action P)     (no-action P)         CONTEXT
             │                 │                  │
             └─────────────────┼──────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │ INCREMENTAL VALUE   │
                    │       ENGINE        │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   ACTION OPTIMIZER  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    POLICY ENGINE    │
                    └──────────┬──────────┘
                               │
                  ┌────────────┼────────────┐
                  ▼            ▼            ▼
                RETRY       MESSAGE      NO ACTION
                  │            │
                  └────────────┼────────────┘
                               ▼
                    ┌─────────────────────┐
                    │    ACTION GATEWAY   │
                    └──────────┬──────────┘
                               │
                               ▼
                         RAZORPAY API
                               │
                               ▼
                            OUTCOME
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
        EXPERIMENTATION                  AUDIT LOG
                │
                ▼
        OUTCOME / LEARNING STORE
                │
                ▼
          MODEL TRAINING (batch)
                │
                ▼
          MODEL VALIDATION
                │
                ▼
        BETTER MODEL → PRODUCTION
```

The same pipeline, written to make the Prediction → Recommendation →
Decision → Execution split explicit (no new components — just naming the
stages that already exist):

```
PaymentEvent            (normalised: PAYMENT_CREATED | PAYMENT_FAILED |
 →                        RETRY_ATTEMPTED | PAYMENT_SUCCEEDED | PAYMENT_CANCELLED)
Recovery Eligibility    (gate — open a RecoveryCase only if eligible)
 → Feature Generation
 → Prediction            (Prediction entity, stamped with ModelVersion)
 → Incremental Value
 → Recommendation        (recommended_action = argmax EIRV)
 → Policy Engine
 → DecisionRecord        (recommended_action AND final_action, may differ)
 → Action Gateway
 → Execution             (Intervention)
 → Outcome
 → Learning Dataset      → Batch Training → Validation → new ModelVersion
```

The LLM (OpenAI) sits beside this pipeline, not inside its control path:

```
LLM: investigation, explanation, message drafting, merchant insights
        ▲
        │ (minimized, privacy-filtered context only)
        │
  DECISION ENGINE (deterministic + ML, owns all financial decisions)
```

### Component map

| Component | Lives in | Technology |
|---|---|---|
| Event ingestion | `backend/api`, `backend/integrations` | FastAPI route + webhook verification; normalises inbound signals to the `PaymentEvent` vocabulary |
| Recovery eligibility | `backend/services` | Python (deterministic gate); decides whether a `PAYMENT_FAILED` opens a `RecoveryCase` |
| Feature pipeline | `backend/services`, `ml/features` | Python/pandas |
| Feature store | PostgreSQL tables | PostgreSQL |
| Recovery / baseline models | `ml/models`, `ml/inference` | scikit-learn, LightGBM |
| Incremental value engine | `backend/decision_engine` | Python |
| Action optimizer (recommendation) | `backend/decision_engine` | Python |
| Policy engine | `backend/policies` | Python (deterministic rules) |
| DecisionRecord writer | `backend/decision_engine` | Python; persists recommended vs final action, per-candidate policy results (model version traced via each Prediction, not stored on the DecisionRecord itself) |
| Action gateway | `backend/integrations` | Python; MVP executes `RETRY` (Razorpay, Phase 8) and `MESSAGE` via a **simulated message gateway**; real WhatsApp/SMS/Email channels and `VOICE`/Sarvam are Phase 10 |
| Outcome / audit / experiment stores | PostgreSQL tables | PostgreSQL; DecisionRecord + ExperimentAssignment (CONTROL/TREATMENT) live here |
| Model training/validation | `ml/training`, `ml/evaluation` | Python, scheduled/manual batch job; each run yields an immutable `ModelVersion` (status DRAFT→VALIDATED→PROMOTED→RETIRED, or →REJECTED; one PROMOTED per model role) |
| Policy / Experiment stores | PostgreSQL tables | PostgreSQL; `Policy` (immutable per version, per merchant), `Experiment` + `ExperimentAssignment` (one per RecoveryCase, immutable) |
| LLM layer | `backend/services` (llm service) | OpenAI API |
| Async processing | `backend/workers` | Redis + Celery/Redis Streams |
| Dashboard | `frontend/` | Next.js, TypeScript, Tailwind, shadcn/ui |

## 4. Alternatives considered

| Area | Alternative | Why rejected |
|---|---|---|
| Event backbone | Kafka | Operational overhead far exceeds hackathon-scale event volume; Redis Streams/Celery is sufficient and far faster to stand up. |
| Feature store | Dedicated feature-store product (Feast, etc.) | Our feature set is small and relational; Postgres tables plus a documented feature pipeline achieve the same guarantees without new infrastructure. |
| Orchestration | Airflow | Batch retraining runs on a simple scheduled/manual script; Airflow's DAG machinery isn't earning its complexity at this scale. |
| Deployment | Kubernetes | Docker Compose is sufficient for a demoable, reproducible local/hackathon deployment. |
| Observability | Prometheus + Grafana | Business metrics (revenue at risk/recovered, incremental revenue) matter far more than infra metrics for this product's story; a dashboard panel covers it. |
| Model tracking | MLflow | Deferred — only introduced if plain versioned model artifacts + a metadata table become unmanageable (see `ml/learning-loop.md`). |
| Agents | Multi-agent framework | A single reasoning layer (LLM for investigation/explanation) plus deterministic services is safer and simpler for a financial decision system; multiple coordinating agents add failure surface without adding capability we need. |
| LLM providers | Multiple providers (OpenAI + Claude, etc.) | One well-integrated provider is enough; splitting adds integration cost with no product benefit at this stage. |

Full reasoning and any future re-litigation lives in
`decisions/architecture-decisions.md`.

## 5. Why this option

This is the architecture from Architecture Decision phase of product
planning (see original planning transcript, "Architecture B + D hybrid"),
approved as-is. It gives us:
- A clean separation between *prediction* (ML), *decisioning* (deterministic
  optimizer + policy), and *execution* (action gateway) — this separation is
  itself part of the product's pitch (auditable, explainable, safe).
- A learning loop that's real but not reckless (batch retrain + validate +
  promote, not per-transaction weight updates).
- Nothing in the stack that can't be explained and justified in the 5-minute
  pitch video.

## 6. Example

See `architecture/decision-flow.md` for a fully worked single-payment trace
through every component above.

## 7. Implementation implications

- Backend code should keep `decision_engine/`, `policies/`, and
  `integrations/` as separate packages with a one-directional dependency:
  decision_engine may call into policies, policies never call back into
  decision_engine or ML code. This keeps the "policy engine can always
  veto" guarantee simple to verify by inspection.
- ML inference code must be callable synchronously from the backend request
  path for MVP (no need for a separate model-serving service yet) — see
  `ml/models.md`.

## 8. Open questions

- Whether Redis Streams or Celery is the better fit once Phase 6 begins —
  current lean is Celery for its simpler task-retry semantics, final call
  deferred to Phase 6 implementation (see ADR placeholder in
  `decisions/architecture-decisions.md`).

## 9. Visual

See the ASCII architecture diagram in section 3 above — it is the
authoritative version of this diagram; any diagram elsewhere in the repo
must match it or be corrected.
