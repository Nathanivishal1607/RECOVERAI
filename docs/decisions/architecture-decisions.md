# Architecture Decision Records

## 1. Purpose

Keep a single, chronological log of every significant decision, so later
contributors (or later phases) don't silently re-litigate settled questions
without knowing why they were settled that way, and so genuine open
questions have one place to be raised and resolved before implementation.

## 2. Context

Many of these decisions were made during pre-implementation product
planning (a long exploratory conversation covering track selection, idea
generation, competitive research, architecture, and tech stack) and are
recorded here for the first time as formal ADRs, backfilled at Phase 0.
Going forward, every new significant decision should be appended here at
the time it's made, not reconstructed later.

## 3. Current decision — ADR log

---

### ADR-000: Build for Track 3 (AI Revenue Recovery), product = RecoverAI

**Decision:** Target the Razorpay Buildathon's Track 3, building an AI
Revenue Recovery Decision Engine, rather than Track 1 (AI Growth & Agentic
Commerce) or Track 2 (AI Risk Manager).

**Alternatives considered:**
- *AI Commerce Gateway for SMBs* (Track 1) — strong idea, but Shopify
  Catalog, OpenAI's ACP/product feeds, and Stripe's Agentic Commerce Suite
  already solve most of "make a merchant AI-readable" for merchants of any
  size (Shopify explicitly opened Catalog sync to non-Shopify merchants).
  A defensible version would require reframing as "AI-commerce
  competitiveness," a bigger, less-validated research bet.
- *AI Fraud Ring Investigator* (Track 2) — strong, technically
  differentiated (graph + ML + explainability), but requires a fraud-
  labelled dataset that's harder to simulate convincingly than payment
  recovery, and a graph-modelling investment beyond hackathon time budget
  alongside everything else.

**Why chosen:** Directly matches Track 3's published bar (detect → diagnose
→ intervene → measure → audit → stop). Produces one clean, defensible
headline metric (incremental revenue recovered). Buildable end-to-end with
synthetic data. Matches team's existing skills (ML/data pipelines, FastAPI,
Sarvam/Indian-language speech-to-text as an optional differentiator).

**Status:** Approved, locked for the hackathon submission.

---

### ADR-001: Backend = Python + FastAPI

**Decision:** FastAPI for all backend/API work.

**Alternatives considered:** Node.js/NestJS.

**Why rejected (Node/NestJS):** Would introduce an unnecessary language/
runtime split between the API layer and the ML stack (scikit-learn,
LightGBM, pandas), which must be called synchronously from the decision
engine's request path. Python end-to-end removes a serialization/RPC
boundary that adds no value here.

**Status:** Approved.

---

### ADR-002: Reject "generic smart retry" as the product framing

**Decision:** RecoverAI is an incremental-value decision engine where retry
is one action among several, not a retry-timing product.

**Context:** Stripe (Smart Retries), Chargebee (Smart Dunning), and
Razorpay's own Subscription Recovery Agent / Intelligent Retry Engine
already ship ML-driven retry timing — discovered during competitive
research after the initial idea had been "AI Payment Recovery
Orchestrator" framed primarily around retries.

**Why chosen:** Reframing around "is intervention worth it, and which
intervention maximizes *incremental* revenue" is not already solved by
these incumbents in the same explicit, incremental/counterfactual way, and
matches Track 3's bar better than a retry-timing pitch would.

**Status:** Approved. See `product/product-overview.md` and
`product/problem-statement.md`.

---

### ADR-003: Optimize for incremental revenue (EIRV), not raw recovery rate

**Decision:** The core metric and decision formula is Expected Incremental
Recovery Value: `[P(recover|action) - P(recover|none)] × amount - cost(action)`.

**Alternatives considered:** Simple Expected Recovery Value
(`P(action) × amount - cost`); multi-objective scoring with soft
friction/risk penalty terms; constrained optimization; contextual bandits;
reinforcement learning. Full comparison table in `ml/uplift-modelling.md`
section 3.

**Why chosen:** Only EIRV directly encodes "don't claim credit for recovery
that would have happened anyway," and it's testable against the
simulator's hidden ground truth without requiring live-experiment
infrastructure (bandits, RL) that's inappropriate to stand up against real
financial actions in hackathon time.

**Status:** Approved, locked. See `ml/uplift-modelling.md`,
`decision-engine/value-calculation.md`.

---

### ADR-004: LLM never controls money; policy engine has unconditional veto

**Decision:** All financial actions pass through deterministic ML
prediction → deterministic value calculation → deterministic policy check,
in that order, before execution. The LLM (OpenAI) is confined to
investigation, explanation, and message drafting, and cannot be overridden
by a high predicted value.

**Alternatives considered:** LLM proposes the action directly; policy
engine overridable by sufficiently high predicted value. Both rejected —
see `architecture/decision-flow.md` section 4 and
`architecture/security-and-safety.md` section 4.

**Status:** Approved, locked. Treated as non-negotiable per top-level
project instructions.

---

### ADR-005: Batch retrain + validate + promote, not per-transaction learning

**Decision:** Every case outcome is recorded immediately; model weights are
only updated via a validated batch retrain-and-promote cycle (N new cases
or a schedule, whichever first; new candidate must not regress on AUC or
calibration vs. current production model).

**Context:** User explicitly asked that "the model should get better after
every case." Retraining literal weights after every single transaction was
considered and rejected as unstable/risky for a financial system; this ADR
records the agreed resolution.

**Status:** Approved. See `ml/learning-loop.md`.

---

### ADR-006: Hackathon-lean infrastructure — no Kafka/Kubernetes/Spark/Airflow/Prometheus/Grafana/MLflow/multi-agent frameworks (for now)

**Decision:** Use Redis Streams/Celery for async processing, Postgres
tables as the feature store, Docker Compose for deployment, plain versioned
model artifacts instead of MLflow, a single LLM provider (OpenAI), and a
single reasoning layer instead of a multi-agent framework.

**Why chosen:** None of these add proven capability at the data
volumes/team size of a hackathon submission; each adds real operational or
integration cost. Full per-technology reasoning in
`architecture/system-architecture.md` section 4.

**Status:** Approved for the hackathon. Explicitly revisitable if the
project ever operates at real production scale — not expected within this
engagement.

---

### ADR-007: Phase 0.5 Specification Corrections

**Context:** The Phase 0 specification was reviewed before any
implementation began. The review found terminology and consistency issues
worth fixing while the spec is still the only artifact — cheaper to correct
now than after the schema and models become code.

**Corrections (documentation only — no code, models, migrations, or
integrations were written):**

- **Clarified S-learner vs T-learner terminology.** The proposed MVP uplift
  approach (one shared model with action as an input feature) is an
  **S-learner-style shared outcome model with intervention/action as a
  treatment feature**, not a "T-learner." Not a permanent commitment;
  Phase 4 experimentally compares S-learner, T-learner, and other suitable
  uplift/treatment-effect approaches, choosing on evaluation results.
  (`ml/models.md`, `ml/uplift-modelling.md`)
- **Standardized the MVP intervention set** to exactly `RETRY`, `MESSAGE`,
  `NO_ACTION`. `MESSAGE` is an abstract intervention delivered via a
  (simulated for MVP) message gateway; `WHATSAPP` / `SMS` / `EMAIL` are
  future channels behind that gateway; `VOICE` is a future distinct action
  and must not be required for the MVP. (`product/*`, `integrations/*`,
  `data/*`, `architecture/*`, decision-engine docs)
- **Distinguished `RecoveryCase` from `TrainingExample`.** A RecoveryCase
  is the central business/audit object; a TrainingExample is a derived ML
  dataset row. One case can produce multiple training observations;
  "one case = one training example" is removed. (`data/data-model.md`,
  `ml/labels.md`)
- **Clarified `PaymentEvent` and payment-attempt handling.**
  `Payment → RecoveryCase` stays 1:1 as an explicit MVP simplification;
  `PaymentEvent` is the authoritative chronological record of the payment
  lifecycle and already supports multiple attempts. No
  `Order → Payment → PaymentAttempt` hierarchy is introduced.
  (`data/data-model.md`, `data/database-schema.md`)
- **Marked simulator costs as illustrative.** Retry/message cost numbers
  are illustrative simulation assumptions only — not Razorpay pricing —
  and are configurable (`SIMULATED_RETRY_COST`, `SIMULATED_MESSAGE_COST`).
  (`decision-engine/value-calculation.md`, `data/synthetic-data.md`,
  `ml/uplift-modelling.md`, `integrations/messaging.md`)
- **Clarified continuous outcome collection vs. batch retraining.**
  "Learning after every case" means every outcome is recorded immediately
  and the training dataset grows continuously; model weights update only
  via a validated batch retrain/validate/promote cycle. A worse model must
  not replace a better one. (`ml/learning-loop.md`)
- **Defined "better model" at a high level** as multi-dimensional
  (predictive quality, calibration, uplift quality, decision quality,
  incremental expected value, realized incremental recovery, policy
  compliance) — not accuracy or AUC alone. (`ml/learning-loop.md`,
  `ml/evaluation.md`)
- **Added explicit stopping and audit requirements.** Stopping conditions
  and the per-decision audit questions are now spelled out.
  (`decision-engine/decision-engine.md`,
  `architecture/security-and-safety.md`)
- **Split Phase 1** into **Phase 1A — Data Contract Finalization** and
  **Phase 1B — Data Layer Implementation**, giving one final design
  checkpoint before the schema becomes code. (`docs/README.md`)

**Decision:** These corrections are adopted as part of the canonical
`/docs` specification. The core architecture, product positioning
(incremental-value revenue recovery decision engine), EIRV as the MVP
economic objective, the LLM-never-moves-money rule, privacy-by-
minimization, and the lean hackathon stack are all **unchanged**.

**Status:** Approved.

---

### ADR-008: Phase 0.6 Data-Contract Clarifications

**Context:** The Phase 0.5 specification was reviewed once more before
data-contract implementation (Phase 1A). Five concepts were implicit or
under-specified and are made explicit now, documentation-only, so the data
contract can be frozen cleanly.

**Decisions:**

1. **`DecisionRecord`** becomes a first-class concept — the auditable
   record of one evaluate→decide cycle (candidate actions, prediction
   ref(s), EIRV per candidate, recommended action, policy result, final
   action, decision reason, model version, timestamp). It exists whether
   or not anything is executed, so a blocked recommendation still has a
   home. Distinct from `Prediction`, `Recommendation` (a field on it),
   `Intervention`/execution, and `Outcome`.
2. **`ModelVersion`** becomes explicit — every `Prediction` and every
   `DecisionRecord` is traceable to the exact model that produced it
   (`model_version_id`, `model_name`, `version`, `status`, `created_at` at
   minimum). May later be accompanied by `feature_schema_version` /
   `decision_engine_version` / `policy_version`; these need not all be
   separate tables in the MVP.
3. **RecoveryCase state machine** is defined explicitly — states,
   transitions, terminal states (`RECOVERED`, `STOPPED`, `EXPIRED`,
   `FAILED`), invalid transitions, and its relationship to `PaymentEvent`,
   `DecisionRecord`, `Intervention`, `Outcome`, and stopping rules.
   `CLOSED` is retained only as an umbrella term for "in a terminal state."
4. **Prediction vs. Recommendation vs. Execution** are three distinct
   concepts, never collapsed into one `action` field. `recommended_action`
   and `final_action` are stored separately and may differ.
5. **`ExperimentAssignment`** (`CONTROL` / `TREATMENT`) is introduced as a
   minimal conceptual hook for observational incremental-effect
   evaluation, kept explicitly separate from the simulator's hidden
   ground truth. No experimentation platform; no statistical method
   locked.

**Rationale:** These concepts are necessary to make incremental-value
decisions explainable, auditable, reproducible, measurable, and evaluable
across model versions.

**Consequence:** Phase 1A must finalize the exact schema, fields, keys,
and relationships for `DecisionRecord`, `ModelVersion`, and
`ExperimentAssignment`, plus the final state-name spelling. No
implementation (models, migrations, code, infrastructure) is done in this
pass. The approved Phase 0 / 0.5 decisions are otherwise unchanged.

**Status:** Approved.

---

### ADR-009: Phase 1A.1 Core Data Contract

**Context:** Phase 1A began with a design-only pass to finalize the
conceptual/logical contract for the four foundational entities —
`Merchant`, `Payment`, `PaymentEvent`, `RecoveryCase` — before Phase 1B
turns any of it into code. Documentation-only; recorded in
`data/data-model.md` "Core data contract — Phase 1A.1".

**Decisions:**

1. **Identity = UUID `id` + human-readable `display_id`.** Internal keys
   and foreign keys use an opaque UUID `id`; a separate unique `display_id`
   (`M-019`, `P-78291`, `RC-10281`) is for humans/dashboards/demo.
   Provider identifiers never become the PK. (`PaymentEvent` is an
   internal append-only record — `id` only.)
2. **Lean internal `Payment.status` with provider mapping.** MVP
   vocabulary: `CREATED`, `PROCESSING`, `FAILED`, `SUCCEEDED`,
   `CANCELLED`. Provider states (`authorized` → `PROCESSING`, `captured` →
   `SUCCEEDED`, `refunded` → out of MVP scope, …) are mapped in, not
   passed through raw. No state added for completeness.
3. **`PaymentEvent` is the authoritative, immutable, append-only
   chronological lifecycle record.** MVP `event_type` vocabulary (smallest
   useful set): `PAYMENT_CREATED`, `PAYMENT_FAILED`, `RETRY_ATTEMPTED`,
   `PAYMENT_SUCCEEDED`, `PAYMENT_CANCELLED`. `PAYMENT_PROCESSING` /
   `PAYMENT_AUTHORIZED` / `PAYMENT_CAPTURED` are deliberately excluded.
   Provider detail lives in `provider_event_id` / `metadata`.
4. **`attempt_number` is nullable** — set on attempt-scoped events
   (`PAYMENT_FAILED`, `RETRY_ATTEMPTED`, `PAYMENT_SUCCEEDED`), `NULL`
   otherwise. `event_timestamp` (when it occurred) is distinct from
   `created_at` (when we ingested it).
5. **RecoveryCase is created via a recovery-eligibility gate.** A
   `PAYMENT_FAILED` event does not automatically create a `RecoveryCase`;
   it triggers a deterministic eligibility check first. Eligibility
   ("should this enter the recovery system?") is a separate stage from
   EIRV ("which action is best for a case that exists"). The full rule set
   is finalised in the decision-engine phase.
6. **At most one *active* `RecoveryCase` per payment** (MVP) — a business
   rule, not a permanent `UNIQUE(payment_id)`; Phase 1B picks the
   constraint/partial index. No `Order` / `PaymentAttempt` /
   `RecoveryEpisode` hierarchy.
7. **RecoveryCase state machine** (confirmed from ADR-008):
   `OPEN → ANALYZING → ACTION_SELECTED → ACTION_EXECUTED →
   WAITING_FOR_OUTCOME`, re-evaluate loop back to `ANALYZING`, terminal
   `RECOVERED` / `STOPPED` / `EXPIRED` / `FAILED`. Core fields: `id`,
   `display_id`, `merchant_id`, `payment_id`, `status`, `opened_at`,
   `closed_at`, `last_evaluated_at`, `expires_at`, `created_at`,
   `updated_at` (`resolved_at` superseded by `closed_at`).
8. **`NO_ACTION` ≠ terminal `STOPPED`.** `NO_ACTION` is a per-evaluation
   decision outcome; the case usually stays observable and may
   re-evaluate. `STOPPED` is a terminal case status (hard stop, no
   revisiting). Never collapsed.
9. **Monetary values are exact decimal** with explicit `currency` — never
   floating-point. (DB precision/scale is Phase 1B.)
10. **Sensitive payment credentials are outside the data model** — no card
    number, CVV, UPI PIN, bank credentials, API/auth secrets, or
    unnecessary PII (reinforces the privacy architecture).

**Consequence:** Phase 1B implements SQLAlchemy models, PostgreSQL types,
foreign keys, indexes, constraints, migrations, repositories, and Pydantic
schemas for these four entities. Later Phase 1A steps (1A.2+) finalize the
remaining entities (`Prediction`, `DecisionRecord`, `Intervention`,
`Outcome`, `Policy`, `ModelVersion`, `Experiment` / `ExperimentAssignment`,
`TrainingExample`) and the full eligibility rule set. No implementation was
done in this pass. All prior approved decisions stand.

**Status:** Approved.

---

### ADR-010: Phase 1A.2 Decision Data Contract

**Context:** After the core data contract (ADR-009), Phase 1A.2 finalized
the conceptual/logical contract for the RecoverAI **decision lifecycle** —
`Prediction`, `DecisionRecord`, `PolicyEvaluation`, `Intervention`,
`Outcome` — so every financial recovery decision is traceable,
explainable, reproducible, auditable, and usable by the future ML learning
loop. Documentation-only; recorded in `data/data-model.md` "Decision data
contract — Phase 1A.2". The EIRV formula (ADR-003) and the LLM/policy
guarantees (ADR-004) are unchanged.

**Decisions:**

1. **One `DecisionRecord` = one evaluate→decide cycle.** It evaluates all
   MVP candidate actions (`RETRY`, `MESSAGE`, `NO_ACTION`) together — not
   three separate DecisionRecords.
2. **`Prediction` is action-specific.** One `Prediction` per candidate
   action per `DecisionRecord` (three per MVP cycle; the `NO_ACTION`
   prediction is the baseline). Each belongs to exactly one
   `DecisionRecord`.
3. **`Prediction` ≠ EIRV.** The ML model produces per-action
   `Prediction`s; the decision engine computes EIRV from them. Flow:
   model → predictions → EIRV → recommendation.
4. **EIRV stays a decision-engine/economic calculation** (ADR-003
   formula unchanged). Its inputs (per-action probability via `Prediction`,
   `payment_amount_at_decision`, `cost_used` per action) and outputs
   (`eirv_value` per action, `recommended_action`, `decision_reason`) are
   **persisted** so "why did RETRY win?" is answerable — and EIRV is
   independently re-derivable — without today's model/policy/config.
5. **`Recommendation` ≠ `final_action`.** `recommended_action`
   (best economic action, pre-policy) and `final_action` (authorized
   decision) are stored **separately** on the `DecisionRecord` and may
   differ.
6. **Policy evaluation is a distinct stage** recorded as `PolicyEvaluation`
   (one per candidate checked): `policy_id`/`policy_version`, `result`
   (`ALLOWED`/`BLOCKED`), machine-readable `reason_code`, `reason`,
   `evaluated_at`. The ML/economic recommendation and the policy
   authorization stay distinct records.
7. **Policy Engine keeps its unconditional veto** (ADR-004 reaffirmed).
8. **`Intervention` = an action actually attempted/executed** (`RETRY` or
   `MESSAGE` only). `DecisionRecord 1 ── 0..1 Intervention` for the MVP.
9. **`NO_ACTION` never creates an `Intervention`.** No fabricated rows.
10. **`execution_status` vocabulary (MVP):** `REQUESTED`, `ACCEPTED`,
    `REJECTED`, `FAILED`. `SUCCEEDED` is excluded — recovery "success" is
    an `Outcome` question, not an execution-status question.
11. **`Outcome` ≠ `execution_status`.** `execution_status = ACCEPTED` with
    `Outcome = NOT_RECOVERED` is normal.
12. **`Outcome` ≠ `RecoveryCase.status`.** `Outcome.result` is its own
    minimal vocabulary (`RECOVERED` / `NOT_RECOVERED`, plus
    `recovery_amount` and `observed_at` for delayed outcomes). Case
    state-machine states are not reused as outcome values. `Outcome`
    attaches to the `DecisionRecord` (so `NO_ACTION` cycles can have one)
    and optionally references the `Intervention`.
13. **A `RecoveryCase` supports multiple `DecisionRecord`s** (one per
    cycle, ordered by `cycle_number`), each with its own predictions,
    model version, EIRV context, recommendation, policy evaluations, final
    action, intervention, and outcome.
14. **Historical `DecisionRecord`s are immutable and never overwritten.**
    Re-evaluation creates a new record.
15. **Every `Prediction` references the exact immutable `ModelVersion`**
    (`model_version_id`), not `model_name` alone. A `DecisionRecord`'s
    model reference is derived from its `Prediction`s.
16. **Version traceability minimum:** `Prediction → exact ModelVersion` +
    `DecisionRecord → policy version` + persisted EIRV inputs/outputs.
    `feature_schema_version` is **derived** (pinned by `ModelVersion`;
    inputs captured in the persisted feature snapshot) — not stored on
    `DecisionRecord`. `decision_engine_version` is **optional metadata**,
    not load-bearing for reconstruction.
17. **Historical economic/policy context stays auditable** — recomputing
    with today's model/policy/cost is not an acceptable substitute for a
    historical explanation. The persisted set above is the smallest
    structure giving reproducibility without duplicating everything.
18. **Decision data remains compatible with future experimentation and ML
    training** — the records provide context/action/outcome/decision-context
    at (context, action, outcome) granularity; one `RecoveryCase` → many
    training observations (Phase 0.5 "one case ≠ one TrainingExample"
    stands). `DecisionRecord` carries a nullable `experiment_assignment_ref`
    for later attribution.
    *[Refined by ADR-011 §9: experiment attribution lives on the
    `RecoveryCase` (one `ExperimentAssignment` per case, immutable), not on
    the `DecisionRecord` — there is no per-`DecisionRecord` experiment
    field; every cycle inherits the case's arm.]*
19. **Four lifecycles stay separate:** payment (`Payment`/`PaymentEvent`),
    recovery (`RecoveryCase` status), decision (`DecisionRecord` →
    `Prediction`/EIRV/`Recommendation`/`PolicyEvaluation`/`final_action`),
    action/outcome (`Intervention` → execution → `Outcome`).
    `DecisionRecord` never duplicates `PaymentEvent`s.
20. **Privacy unchanged** — decision records use internal references, not
    duplicated sensitive data; the `Prediction` feature snapshot follows
    the existing data classification; the LLM stays outside the
    authoritative decision/execution path.

**Consequence:** Phase 1B implements SQLAlchemy models, PostgreSQL types,
keys, indexes, constraints, migrations, repositories, and Pydantic schemas
for these entities. The full `ModelVersion`, `Experiment` /
`ExperimentAssignment`, `TrainingExample`, and `Policy` contracts are
later phases (1A.3+). No implementation was done in this pass. All prior
approved decisions stand; no contradictions with Phase 0/0.5/0.6/1A.1 were
found.

**Status:** Approved.

---

### ADR-011: Phase 1A.3 Model, Policy & Experiment Data Contract

**Context:** After the decision data contract (ADR-010), Phase 1A.3
finalized the conceptual/logical contract for `ModelVersion`, `Policy`,
`Experiment`, and `ExperimentAssignment`, so that historical decisions
stay reproducible and auditable and controlled experimentation can never
weaken safety. Documentation-only; recorded in `data/data-model.md`
"Model, Policy & Experiment data contract — Phase 1A.3". EIRV (ADR-003),
the LLM/policy guarantees (ADR-004), and everything in
Phase 0/0.5/0.6/1A.1/1A.2 are unchanged.

**Decisions:**

1. **`ModelVersion` immutability.** A `ModelVersion` is one exact,
   reproducible model. Its artifact identity, checksum, training dataset
   identity, feature schema identity, algorithm, hyperparameters, training
   config, training code/pipeline identity, evaluation results, and
   reproducibility metadata are **immutable**. A material change is a
   **new** `ModelVersion`, never an edit.
2. **`ModelVersion` lifecycle.** `status ∈ {DRAFT, VALIDATED, PROMOTED,
   RETIRED, REJECTED}` — the **only** mutable field. Allowed transitions:
   `DRAFT→VALIDATED`, `DRAFT→REJECTED`, `VALIDATED→PROMOTED`,
   `VALIDATED→REJECTED`, `PROMOTED→RETIRED`. Forbidden: `REJECTED→PROMOTED`
   (and any exit from `REJECTED`), `RETIRED→PROMOTED`, `DRAFT→PROMOTED`
   (must pass `VALIDATED`).
3. **One production default per model role.** For a given `model_role`
   there is **exactly one** `PROMOTED` `ModelVersion` at a time. This does
   **not** cap how many `ModelVersion`s exist — `VALIDATED` candidates and
   `RETIRED` history coexist.
4. **Model reproducibility.** `ModelVersion` must conceptually carry:
   model identity, model role, algorithm/type, artifact reference,
   artifact checksum/hash, training dataset snapshot identity, feature
   schema version/identity, training config/hyperparameters, training
   code/pipeline version, random seed where relevant, evaluation summary,
   lifecycle status, creation timestamp. Given a historical `Prediction`
   we can identify the exact `ModelVersion` and, from it, the exact
   artifact/data/schema/config for reproduction. No full ML registry is
   built for the hackathon.
5. **Rejected cannot become promoted.** A `REJECTED` `ModelVersion` cannot
   later be changed to `PROMOTED`. Retraining produces a **new**
   `ModelVersion`.
6. **`Policy` version immutability.** A `Policy` version is immutable; a
   policy change creates a **new** version. Historical versions are never
   edited in place.
7. **Structured policy data vs. Policy Engine logic.** `Policy` is
   configurable structured rule data (WHAT is allowed — categories:
   max retries, max contacts, recovery window, min/max amount, allowed
   actions/channels, restricted hours, consent requirements, risk
   thresholds, merchant limits). The Policy Engine is the fixed
   deterministic code that evaluates it (HOW). **No arbitrary executable
   policy code** in the MVP design.
8. **Merchant-specific policy versioning.** A `Policy` belongs to a
   `Merchant`; a merchant has multiple historical `Policy` versions; the
   system can identify which exact version was evaluated for any
   historical decision (already via `PolicyEvaluation.policy_version`,
   Phase 1A.2, unchanged). Policy identity is **not** moved onto
   `Intervention`.
9. **`ExperimentAssignment` at the `RecoveryCase` level.** Assignment is
   one per `RecoveryCase`, **never** per `DecisionRecord`. Every
   `DecisionRecord` under a case inherits the case's arm. This refines the
   ADR-010 placeholder ("`DecisionRecord` carries a nullable
   `experiment_assignment_ref`"): there is **no** per-`DecisionRecord`
   experiment field; attribution is read through the `RecoveryCase`.
10. **`CONTROL` / `TREATMENT` semantics.** `CONTROL` = the existing/default
    strategy (comparison baseline); `TREATMENT` = the experimental
    strategy under evaluation. "Treatment" does **not** mean "force an
    action" — it may vary the `ModelVersion`, decision strategy, or
    economic configuration. Both arms keep the full candidate set.
11. **Immutable experiment assignment.** Once assigned, a case's arm never
    changes (`CONTROL → TREATMENT` mid-case is unsupported). Historical
    assignment stays auditable.
12. **`recovery_case.experiment_arm` superseded.** `ExperimentAssignment`
    is the sole authoritative assignment record. The legacy string field
    is superseded (drop or keep-as-mirror is a Phase 1B mechanics choice);
    it must not be a second source of truth.
13. **`NO_ACTION` remains available in experiments.** Every decision cycle
    in every arm retains `RETRY` / `MESSAGE` / `NO_ACTION`. An experiment
    must never force an intervention because a case is in `TREATMENT`.
14. **Policy always overrides experimentation.** Ordering is unchanged:
    experimental strategy → predictions → EIRV → recommendation → **policy
    evaluation (ADR-004 unconditional veto)** → final action. Not
    `Experiment → Final Action`. Experiments also never bypass recovery
    eligibility, safety constraints, merchant restrictions, risk limits, or
    privacy rules.
15. **Offline-first experimentation for the hackathon.** Primary safe
    mechanism: offline over historical/synthetic data (run CONTROL vs
    TREATMENT strategies, compare outcomes / incremental value). Controlled
    *production* experimentation is a documented future capability
    constrained by eligibility, policy, risk limits, small controlled
    cohorts, and auditability — never unrestricted live exploration, never
    RL-style unconstrained exploration.
16. **Immutability/audit principles restated (explicit).** Immutable:
    `ModelVersion` (except `status`), `Policy` version, `Prediction`,
    `DecisionRecord`, `ExperimentAssignment`, historical `PolicyEvaluation`,
    historical `Intervention` (once resolved), `Outcome` (once resolved).
17. **Experimental model versions by reference.** A `TREATMENT` arm may
    use a `VALIDATED` (not `PROMOTED`) `ModelVersion` without changing the
    production default; the assignment/config references the immutable
    `ModelVersion` — it does **not** duplicate its data.

**Preserved unchanged:** MVP actions `RETRY`/`MESSAGE`/`NO_ACTION`; EIRV as
the economic objective computed by the decision engine; `Prediction ≠ EIRV
≠ Recommendation ≠ Final Action ≠ Intervention`; `NO_ACTION` never creates
an `Intervention`; `NO_ACTION ≠ STOPPED`; Policy Engine unconditional veto;
LLM never authorizes/executes financial actions; `PaymentEvent`
authoritative for payment lifecycle; one `RecoveryCase` → multiple
`DecisionRecord`s; `Prediction → exact immutable ModelVersion`;
`DecisionRecord`'s model reference derived from its `Prediction`s;
historical records not overwritten.

**Consequence:** Phase 1B implements SQLAlchemy models, PostgreSQL types,
keys, indexes, constraints (incl. "one `PROMOTED` per `model_role`" and
"one `ExperimentAssignment` per `RecoveryCase`"), migrations, repositories,
and Pydantic schemas. `TrainingExample` and the full recovery-eligibility
rule set remain later phases. No implementation was done in this pass.
*[`TrainingExample` subsequently finalized by ADR-012.]*

**Status:** Approved.

---

### ADR-012: Phase 1A.4 Training Data Contract

**Context:** The last data-contract phase before implementation. Defines
how completed decision cycles become valid ML training observations —
deliberately narrow (no feature store, MLflow, Kafka/Spark, warehouse,
distributed training, RL, or production ML platform). Documentation-only;
recorded in `data/data-model.md` "Training data contract — Phase 1A.4".
Everything in Phase 0…1A.3 is unchanged.

**Decisions:**

1. **`TrainingExample` is the ML observation unit** — a derived,
   **immutable** row built from the immutable `DecisionRecord` /
   `Prediction` / `Outcome` / `RecoveryCase` / `ExperimentAssignment`
   records.
2. **Preferred logical granularity: `DecisionRecord × candidate action`**
   — one `TrainingExample` per `(cycle, action)` for `RETRY`, `MESSAGE`,
   `NO_ACTION`. Physical layout (per-action rows vs. a compact
   per-`DecisionRecord` row with an `action` column) is Phase 1B; the
   logical unit is `(cycle, action)`. Not "one `RecoveryCase` = one
   `TrainingExample`" (Phase 0.5 stance held), not a three-action blob.
3. **A `Prediction` is not an observed outcome.** In a cycle, we observed
   the outcome of the **one** action taken only.
4. **Only actually-observed actions receive an outcome label.** The
   `observed_action` row carries `outcome_label` (from `Outcome.result`);
   the other candidates' rows carry **no** label (no manufactured
   counterfactuals). Never write three `RECOVERED` labels because one
   `RETRY` recovered.
5. **`observed_action` is derived from what happened, not the
   recommendation** (`recommended=RETRY, policy=BLOCKED, final=NO_ACTION`
   ⇒ `observed_action = NO_ACTION`).
6. **`NO_ACTION` can produce a valid training observation without an
   `Intervention`** — a `final_action = NO_ACTION` cycle that resolves an
   `Outcome` yields a labelled `NO_ACTION` row. (`NO_ACTION ≠ STOPPED`;
   `NO_ACTION → no Intervention` — unchanged.)
7. **Failed execution ≠ clean treatment observation.** `final_action`
   `RETRY`/`MESSAGE` with `execution_status ∈ {REJECTED, FAILED}` is not
   labelled as an observed `RETRY`/`MESSAGE` outcome (decision-to-execute
   ≠ execution-success ≠ recovery-outcome). No causal-censoring machinery
   for the MVP.
8. **Repeated `DecisionRecord`s each produce observations** — cycles are
   not collapsed into one row.
9. **`RecoveryCase` is the grouping unit for leakage-safe splitting** —
   train/validation/test splits are made at `RecoveryCase` level; all
   rows of a case stay in one split.
10. **`Outcome` is the recovery label** (`RECOVERED`/`NOT_RECOVERED`,
    `recovery_amount`, `observed_at`) — never `RecoveryCase.status`, never
    `Intervention.execution_status`.
11. **Delayed outcomes are supported** — a `TrainingExample` is not final
    until the cycle's observation window resolves.
12. **`experiment_arm` is inherited from the `RecoveryCase`'s
    `ExperimentAssignment`** (case-level, Phase 1A.3) — no
    `DecisionRecord → ExperimentAssignment` relation.
13. **Simulator hidden ground truth ≠ observational outcomes.** The
    simulator provides true potential outcomes under every action
    (evaluation only); the `TrainingExample` set provides only the
    observed action's outcome per cycle. The observational dataset does
    not supply counterfactuals; no statistical framework is added here.
14. **Feature snapshots represent information available at decision time**
    — the `Prediction.feature_snapshot` frozen as of the `DecisionRecord`.
    The `Outcome` is the label, never a feature; no later
    `Outcome`/`recovery_amount`/`observed_at`, future
    `DecisionRecord`/`PaymentEvent`, or later intervention result may leak
    into features.
15. **Training dataset snapshots are identifiable/reproducible** — the
    exact `TrainingExample` set used to train a model has a deterministic
    content-hash / equivalent identity, referenced by
    `ModelVersion.training_dataset_snapshot_id`. No dataset registry built.
16. **S-learner compatibility preserved** — `(features, action, observed
    outcome)` triples feed the S-learner directly; the Phase 4
    S-learner/T-learner/other comparison is unchanged and consumes the
    same rows.

**Preserved unchanged:** MVP actions `RETRY`/`MESSAGE`/`NO_ACTION`; EIRV as
the economic objective computed by the decision engine; `Prediction ≠ EIRV
≠ Recommendation ≠ Final Action ≠ Intervention`; `NO_ACTION` never creates
an `Intervention`; `NO_ACTION ≠ STOPPED`; Policy Engine unconditional veto;
LLM outside financial authorization/execution; `PaymentEvent`
authoritative; one `RecoveryCase` → multiple `DecisionRecord`s; `Prediction
→ exact immutable ModelVersion`; `DecisionRecord`'s model reference derived
from its `Prediction`s; `ExperimentAssignment` at `RecoveryCase` level;
`REJECTED` `ModelVersion` cannot become `PROMOTED`; historical records not
overwritten.

**Consequence:** Phase 1B / ML implementation fixes the physical
`training_example` shape and types, the exact dataset-snapshot hash
mechanism, split ratios/seed, and any uplift-estimator details. **Phase 1A
(the data contract) is complete.** No implementation was done in this pass.

**Status:** Approved.

---

### ADR-013: Phase 3 — ML Model, Training & Decision-Engine Integration

**Context:** With the Phase 1A contract, Phase 1B persistence, and the
Phase 2 simulator all complete, Phase 3 turns the observational synthetic
data into a real model and a working decision path — the **minimum viable
learning loop**. Implementation phase; no contract changes.

**Decisions:**

1. **Model = one logistic-regression S-learner.** A single scikit-learn
   `Pipeline(StandardScaler → LogisticRegression)` over
   `[decision-time features ⊕ one-hot(candidate action)]`, predicting
   `P(recovery | features, action)` for `RETRY` / `MESSAGE` /
   `NO_ACTION`. Chosen over LightGBM / a model zoo per `ml/models.md`
   Step 1 ("simplest possible, interpretable, fast to validate") and the
   hackathon non-goals. `liblinear` solver + fixed `random_state` →
   deterministic. No deep learning, no T-learner comparison (Phase 4), no
   calibration wrapper yet.
2. **Feature representation = `sim-feature-schema-v1` + action.** The 18
   observable decision-time fields already produced by
   `simulation/features.py`, plus the candidate action as the treatment
   feature. A frozen column order is recorded conceptually via
   `ModelVersion.feature_schema_id`. `ml/features/schema.py` is the single
   vectorizer shared by training and inference. It re-runs a leakage
   guard (defence in depth over the simulator's own).
3. **Training data = persisted `TrainingExample` rows only.** Only rows
   with `is_observed = true` **and** a non-null `outcome_label` become
   labelled `(features, action, outcome)` triples — no manufactured
   counterfactuals (ADR-012 upheld). Features come from the immutable
   `feature_snapshot`. Train/validation/test splitting is **case-level**
   by `recovery_case_id` (mirrors `TrainingExampleRepository.split_by_case`).
4. **`ModelVersion` is the sole registry.** Training calls the existing
   `ModelVersionRepository.create(status=DRAFT)`. **No new table, no
   second registry, no `model_version_id` column on `DecisionRecord`** —
   `Prediction → exact immutable ModelVersion`, and a `DecisionRecord`'s
   model reference stays derived from its `Prediction`s (ADR-010 upheld).
   The lifecycle (`DRAFT → VALIDATED → PROMOTED → RETIRED` / `→ REJECTED`,
   one `PROMOTED` per `model_role`, `REJECTED`/`RETIRED`/`DRAFT` cannot
   jump to `PROMOTED`) is unchanged and enforced by the existing repo.
5. **Artifact reproducibility = local joblib + sha256.** The fitted
   pipeline is dumped to `ml/models/artifacts/<name>-<version>.joblib`
   (already git-ignored) and a sha256 of the file bytes is stored on
   `ModelVersion.artifact_checksum`. Inference loads a model **from its
   `ModelVersion`'s `artifact_ref`**, verifies the checksum, and is
   deterministic across reloads. Historical reconstruction never depends
   on "the latest" model. No cloud artifact store, no model-serving
   platform.
6. **Evaluation is split in two, and the leak boundary is preserved.**
   `ml/evaluation/` computes **observational** metrics only (ROC-AUC,
   log loss, Brier, coarse ECE, per-action mean-probability separation)
   from the held-out `TrainingExample` split — it imports neither
   `simulation.ground_truth` nor `simulation.evaluation`. The
   oracle-vs-model **decision-quality** report lives in
   `simulation/evaluation/model_report.py` (the sanctioned ground-truth
   reader). Hidden truth therefore still flows strictly *out* to
   evaluation and never *into* training features / labels / persisted
   predictions. `tests/simulation/test_dependency_rules.py` still passes,
   and a Phase 3 test re-asserts it for the new modules.
7. **Decision engine = deterministic, model does probabilities only.**
   `backend/decision_engine/` implements `value_engine` (EIRV, the fixed
   ADR-003 formula; `EIRV(NO_ACTION) ≡ 0`), `optimizer` (rank by EIRV,
   `NO_ACTION` always kept as the fallback), and `orchestrator`
   (`DecisionEngine.run_cycle`): call `ml.inference` for the three
   per-action probabilities → persist one `Prediction` per action (all
   referencing the same exact `ModelVersion`) → compute EIRV →
   `recommended_action = argmax` → **policy veto loop** → `final_action`
   → finalize the `DecisionRecord` (recommended & final stored
   separately, `value_context` per action, one `PolicyEvaluation` per
   candidate the loop checked) → create an `Intervention` **only** when
   `final_action ∈ {RETRY, MESSAGE}`. The model never computes EIRV and
   never authorizes an action.
8. **Policy Engine keeps its unconditional veto (ADR-004 upheld).**
   `backend/policies/engine.py` is a pure function of (action, `Policy`
   row, context). `NO_ACTION` is unconditionally `ALLOWED`, guaranteeing
   the veto loop terminates. Hard binary checks only. Zero imports from
   `backend.decision_engine` / `ml` (verified by test), matching the
   dependency rule in `architecture/component-architecture.md`.
9. **Cost model.** The decision engine's default action costs
   (`RETRY 2.0`, `MESSAGE 3.0`, `NO_ACTION 0.0`) mirror
   `simulation.config.SimConfig` so the baseline engine and the simulator
   agree. These are **simulation parameters, not Razorpay pricing**
   (ADR-007 / `value-calculation.md` unchanged).

**Preserved unchanged:** MVP actions `RETRY`/`MESSAGE`/`NO_ACTION`; EIRV
as the economic objective computed by the decision engine; `Prediction ≠
EIRV ≠ Recommendation ≠ Final Action ≠ Intervention`; per-action
`Prediction` bound to an exact immutable `ModelVersion`; `DecisionRecord`
has no independent model-version column; `NO_ACTION` never creates an
`Intervention`; `execution_status` has no `SUCCEEDED`; multiple immutable
`DecisionRecord`s per case; case-level training splits; append-only
`PaymentEvent`; the hidden-ground-truth boundary; the 17-table schema (no
new entities).

**Intentionally deferred (documented, not built):** uplift / incremental
modelling and the S-learner vs T-learner vs other comparison (Phase 4);
LightGBM candidate model and a calibration wrapper (`ml/models.md` Steps
2–3, Phase 3/4 — logistic regression is the shipped MVP model);
automated retrain trigger, promotion gating on a metrics bundle, and the
`ModelVersion` provenance for a real dataset registry (Phase 7); a
production-side feature builder that replaces the simulator's
`build_feature_snapshot` for live traffic (Phase 6/8); the model-vs-oracle
report currently uses a coarse cycle-1 proxy snapshot (only
`failure_category` + `amount` are in the sidecar), so its action
distribution is not representative — the full decision engine on a full
snapshot selects all three actions appropriately (shown in the Phase 3
integration test).

**Status:** Approved (implementation).

---

### ADR-014: Phase 4 — Incremental / Uplift Modelling

**Context:** Phase 3 shipped an S-learner + a deterministic decision
engine. Phase 4 answers "how much *additional* recovery does each action
give vs `NO_ACTION`?" and picks the best learner on **decision quality**,
not classification score. Implementation phase; no contract changes.
2-day hackathon scope — no causal-inference research framework.

**Decisions:**

1. **Incremental probability is derived, never stored, never an EIRV
   substitute.** `incremental(action) = P(recovery | features, action) −
   P(recovery | features, NO_ACTION)`, computed at inference time from a
   model's per-action probabilities. `Prediction.recovery_probability`
   stays the model's per-action probability (ADR-010 unchanged). The
   Decision Engine still computes EIRV from the three `Prediction`s via
   the fixed ADR-003 formula; the ML layer never ranks actions or
   authorizes anything.
2. **Four candidates, one interface.** `ml/models/uplift.py` defines
   `IncrementalModel` (`predict_all_actions` + `incremental`):
   - `s_learner` — the Phase 3 `RecoveryModel` (one shared
     logistic-regression, action one-hot as a treatment feature),
     wrapped for the common interface.
   - `t_learner` — **one `StandardScaler→LogisticRegression` head per
     action**, each fitted ONLY on that action's observed rows. An
     action with <2 rows or a single outcome class falls back to a
     constant base rate. `incremental` differences the heads.
   - `tree_s_learner` — a shallow `DecisionTreeClassifier` S-learner
     (`max_depth=5`, `min_samples_leaf=20`). This is the "tree / uplift
     candidate" done cleanly with the existing stack — **not** a bespoke
     causal-tree / EconML framework (explicitly out of scope). Its
     per-action probability differences are an implicit uplift estimate.
   - `lgbm_s_learner` — a deterministic (`n_jobs=1`, `deterministic=True`,
     fixed seed) action-conditioned `LGBMClassifier`, evaluated only if
     `lightgbm` is importable (it was added to `ml/requirements.txt`).
3. **Same observational contract as Phase 3 (ADR-012).** Every candidate
   is fed only `(features, observed_action, observed_outcome)` rows from
   `ml.data.dataset` — no manufactured counterfactual labels,
   decision-time features only, `NO_ACTION` a real action, case-level
   train/val/test split.
4. **Dataset-snapshot id is now a content hash.** `dataset_snapshot_id`
   hashes each row's `(feature_snapshot, action, label)` instead of its
   random-UUID `decision_record_id`, so a fixed simulator seed/config
   yields a reproducible `ModelVersion.training_dataset_snapshot_id`
   across runs. Case-level splitting keys on `RecoveryCase.display_id`
   (a deterministic creation-order counter) for the same reason.
   `backend/repositories/training.py::snapshot_id` (row-id based, stable
   only within one run) is unchanged — it serves a different, in-run
   purpose and no contract depends on cross-run stability there.
5. **Reuse `ModelVersion`, no new entity, no new registry.**
   `ml/training/uplift.py::train_uplift_model(db, kind=...)` writes a
   kind-tagged joblib artifact (`ml/models/artifact.py`) + sha256 and
   registers a `DRAFT` `ModelVersion` under the **same** `model_role =
   "recovery_prediction"` (so "one PROMOTED per role" still holds). The
   lifecycle (`DRAFT→VALIDATED→PROMOTED→RETIRED` / `→REJECTED`, no
   `REJECTED→PROMOTED`, no `DRAFT→PROMOTED`) is the existing repo's,
   unchanged. `ml.inference.load_for_model_version` dispatches on the
   artifact `kind`, still checksum-verified, still deterministic on
   reload; v1 Phase-3 artifacts still load.
6. **Oracle is evaluation-only, and physically walled off.** Observational
   metrics live in `ml/evaluation/compare.py` and import no simulator
   truth. The hidden-truth comparison lives in
   `simulation/evaluation/uplift_report.py` +
   `simulation/evaluation/phase4_compare.py` — the sanctioned readers.
   `tests/simulation/test_dependency_rules.py` (nothing under `ml/` or
   `backend/` imports `simulation.ground_truth` / `simulation.evaluation`)
   still passes; a Phase 4 test re-asserts it for the new `ml/` modules.
   The Oracle is read only AFTER every model's predictions are produced.
7. **Metrics bundle — decision quality is primary.** Per model, on the
   held-out TEST split: Brier / ROC-AUC / ECE (predictive); incremental
   MAE & RMSE vs oracle incremental probability, per action (incremental
   quality); model EIRV-argmax action vs oracle best action = **action
   agreement**, and per-case **EIRV regret** = `oracle_best_EIRV −
   chosen_action_EIRV` scored under hidden truth (decision quality); plus
   the model's RETRY/MESSAGE/NO_ACTION choice mix.
8. **Selection = lowest mean EIRV regret among non-degenerate
   candidates, then highest action agreement, then lowest incremental
   MAE.** A candidate that funnels >90% of cases to one action is
   "degenerate" (its low regret is just the modal-action base rate) and
   is excluded from selection. Predictive metrics break a near-tie only —
   **not** ROC-AUC alone.
9. **Selected model: `t_learner`.** Over seeds 42/7/123 (1500 cases,
   70/15/15 case-level split): Brier 0.174, ROC-AUC 0.804, ECE 0.049,
   incremental MAE 0.193, **action agreement 0.734**, **mean EIRV regret
   54.4** — best on every axis, with a realistic action mix (RETRY ~33% /
   MESSAGE ~55% / NO_ACTION ~11%). `s_learner` collapses to MESSAGE,
   `tree_s_learner` to NO_ACTION, `lgbm_s_learner` is near-degenerate
   (>90% MESSAGE on 2/3 seeds). The T-learner is promotable through the
   unchanged lifecycle and feeds the unchanged Decision Engine (verified
   by `tests/integration/test_phase4_decision_engine.py`).

**Preserved unchanged:** the EIRV formula (ADR-003); `Prediction ≠ EIRV ≠
Recommendation ≠ Final Action ≠ Intervention`; per-action `Prediction`
bound to an exact immutable `ModelVersion`; `DecisionRecord` has no
model-version column; Policy Engine unconditional veto; `NO_ACTION` never
creates an `Intervention`; case-level splitting; append-only
`PaymentEvent`; the hidden-ground-truth boundary; the 17-table schema (no
new entities); case-level `ExperimentAssignment` (not touched — the
Phase 4 comparison is offline over the simulator, no live experiment).

**Known limitations / deferred:** the T-learner's `NO_ACTION` head has the
fewest rows and drives every incremental, so its estimates are the most
seed-sensitive of the three heads (mitigated but not eliminated by the
larger 1500-case config). The tree candidate is a plain sklearn tree, not
an uplift tree; Qini/AUUC curves and an EconML comparison are out of
hackathon scope. Automated promotion gating on the full metrics bundle is
Phase 7. A production-side feature builder (vs the simulator snapshot) is
Phase 6/8. **Update (Phase 5):** the T-learner was promoted and is the
current live/production `ModelVersion` feeding the Decision Engine
(`model_name="recovery-t-learner-logreg"`, `algorithm=
"logistic_regression_per_action"`); the Phase 3 S-learner was retired
from `PROMOTED` and remains available only as a `VALIDATED` baseline for
comparison, per the evaluation above.

**Status:** Approved (implementation).

---

### ADR template (for future entries)

```
Decision:
Context:
Alternatives considered:
Why chosen / why rejected:
Status: Proposed | Approved | Superseded by ADR-0XX
```

## 4. Alternatives considered

Not applicable at the document level — see each ADR's own alternatives.

## 5. Why this option

A single running log (rather than one file per decision) keeps the full
decision history skimmable in one place, which matters more than per-ADR
file isolation at this project's scale.

## 6. Example

See ADR-000 through ADR-012 above.

## 7. Implementation implications

Any phase that proposes deviating from an "Approved, locked" ADR must
raise it here as a new ADR entry (status: Proposed) and get it resolved
before implementing the deviation — per the top-level instruction not to
silently make major architectural decisions.

## 8. Open questions

Tracked per-document (each doc's own section 8) rather than duplicated
here; this file is for settled decisions plus explicitly still-open ones
worth flagging at the architecture level:

- Redis Streams vs. Celery, final call deferred to Phase 6 (see
  `architecture/system-architecture.md` open questions).
- Exact Razorpay API surface for "retry"/action-triggering, deferred to
  Phase 8 research (see `integrations/razorpay.md`).

## 9. Visual

Not applicable — this is a chronological log document.
