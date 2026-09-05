# Database Schema

## 1. Purpose

Translate `data/data-model.md` into concrete PostgreSQL table definitions,
as the reference implementers should follow in Phase 1B/6. The tables for
concepts finalized in Phase 1A.2/1A.3/1A.4 (`model_version`,
`decision_record`, `policy` versioning, `experiment`,
`experiment_assignment`, `training_example`) are deliberately left as
sketches for Phase 1B.

## 2. Context

This is a specification, not yet a migration file — actual SQLAlchemy models
and Alembic (or equivalent) migrations are created in **Phase 1B** (Data
Layer Implementation), after **Phase 1A** (Data Contract Finalization) has
signed off the entities, relationships, identifiers, and lifecycle. Field
names here should be treated as binding unless a documented reason changes
them. See the phase list in `docs/README.md` and ADR-007.

## 3. Current decision — schema (illustrative DDL)

> **Phase 1A.1 note.** The `merchant`, `payment`, `payment_event`, and
> `recovery_case` sketches below reflect the finalized *core data
> contract* in `data/data-model.md` (ADR-009): internal `id` (UUID) +
> human-readable `display_id`; lean internal `payment.status` vocabulary;
> the 5-value `payment_event` vocabulary with nullable `attempt_number`;
> `event_timestamp` vs `created_at`; the extra `recovery_case` lifecycle
> timestamps. The SQL **types, keys, indexes, and constraints shown are
> still illustrative** — Phase 1B fixes them.

```sql
-- id: opaque internal UUID (never provider-derived).
-- display_id: human-readable unique code (e.g. 'M-019') for humans/dashboards.
CREATE TABLE merchant (
    id                  UUID PRIMARY KEY,
    display_id          TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | INACTIVE
    industry            TEXT,                            -- optional context
    currency            TEXT NOT NULL DEFAULT 'INR',     -- optional context
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Non-core tables (policy, customer, model_prediction, intervention,
-- outcome, ...) keep their existing sketch identity for now; the UUID id +
-- display_id treatment is extended to them in later Phase 1A steps.
-- FK targets that point at a Phase 1A.1 core table use its new `id` column.
-- Phase 1A.3 (ADR-011): a Policy VERSION is immutable. A policy change
-- creates a NEW row (new policy_version), never an edit to an existing
-- one. Exactly one version per merchant is "current active"
-- (is_active = TRUE); historical versions are kept for
-- PolicyEvaluation.policy_version traceability (Phase 1A.2). This table
-- is the POLICY DATA (what is allowed) — decision-engine/policy-engine.md
-- is the POLICY ENGINE (how it's evaluated); no executable policy code.
CREATE TABLE policy (
    policy_id                TEXT PRIMARY KEY,       -- identifies the policy "slot" (which rule set)
    policy_version            TEXT NOT NULL,           -- immutable version identity (e.g. 'v3')
    merchant_id              UUID NOT NULL REFERENCES merchant(id),
    max_retry_count          INT NOT NULL DEFAULT 2,
    max_customer_contacts    INT NOT NULL DEFAULT 2,
    contact_window_days      INT NOT NULL DEFAULT 7,
    allowed_interventions    TEXT[] NOT NULL DEFAULT ARRAY['RETRY','MESSAGE'],
    allowed_channels         TEXT[],            -- e.g. simulated/whatsapp/sms/email — post-MVP
    restricted_hours         JSONB,             -- e.g. no contact 22:00-08:00 — conceptual, not final shape
    minimum_amount           NUMERIC,
    max_autonomous_amount    NUMERIC,          -- above this, escalate instead of auto-act
    consent_required_actions TEXT[],            -- e.g. ['VOICE'] — post-MVP
    risk_threshold            NUMERIC,
    case_expiry_days         INT NOT NULL DEFAULT 14,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,   -- TRUE for exactly one version per merchant
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
    -- Immutable once created: no UPDATE of rule fields — only is_active
    -- may flip (superseded by a newer version) and even that is a
    -- Phase 1B mechanics question, not a rule-content edit.
);

CREATE TABLE customer (
    customer_id                 TEXT PRIMARY KEY,
    merchant_id                 UUID NOT NULL REFERENCES merchant(id),
    transaction_count           INT NOT NULL DEFAULT 0,
    successful_transactions     INT NOT NULL DEFAULT 0,
    failed_transactions         INT NOT NULL DEFAULT 0,
    average_transaction_value   NUMERIC,
    historical_recovery_rate    NUMERIC,
    preferred_language          TEXT,
    preferred_channel           TEXT,
    consent_voice                BOOLEAN NOT NULL DEFAULT FALSE,
    last_payment_at             TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
    -- Deliberately NO name, phone, email, card, or bank fields here.
    -- If ever needed, they belong in a separate, more tightly access-controlled
    -- table, never joined into ML feature queries. See architecture/privacy-architecture.md.
);

CREATE TABLE payment (
    id                  UUID PRIMARY KEY,
    display_id          TEXT NOT NULL UNIQUE,           -- e.g. 'P-78291'
    merchant_id         UUID NOT NULL REFERENCES merchant(id),
    customer_id         TEXT NOT NULL REFERENCES customer(customer_id),
    external_payment_id TEXT,               -- provider/Razorpay id; NOT the PK; may be NULL for synthetic
    amount              NUMERIC NOT NULL,   -- exact decimal, never float (Phase 1B fixes precision/scale)
    currency            TEXT NOT NULL,      -- ISO 4217, always explicit
    payment_method      TEXT,               -- e.g. UPI, CARD, NETBANKING
    payment_method_type TEXT,
    status              TEXT NOT NULL,      -- internal vocab: CREATED | PROCESSING | FAILED | SUCCEEDED | CANCELLED
                                            -- (provider states are mapped in — see data/data-model.md)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    -- NO card number / CVV / UPI PIN / bank credentials / secrets — see privacy-architecture.md
);

-- payment_event is the AUTHORITATIVE, immutable, append-only chronological
-- record of the payment lifecycle. payment.status is a convenience
-- denormalization of the latest known state; the ordered payment_event
-- stream is the source of truth. Never UPDATE/DELETE a row here — a
-- correction is a new event. See data/data-model.md.
CREATE TABLE payment_event (
    id                UUID PRIMARY KEY,    -- internal record; no display_id needed
    payment_id        UUID NOT NULL REFERENCES payment(id),
    event_type        TEXT NOT NULL,       -- MVP vocab: PAYMENT_CREATED | PAYMENT_FAILED |
                                           -- RETRY_ATTEMPTED | PAYMENT_SUCCEEDED | PAYMENT_CANCELLED
    event_timestamp   TIMESTAMPTZ NOT NULL,-- when the event OCCURRED (provider/world time)
    attempt_number    INT,                 -- payment attempt this event belongs to; NULL when not attempt-scoped
    amount            NUMERIC,             -- amount in play for this event (usually = payment.amount)
    currency          TEXT,
    provider_event_id TEXT,                -- external event id, for idempotency/audit
    metadata          JSONB,               -- safe provider payload / error codes / context
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()  -- when OUR system ingested it (may be later)
);

CREATE TABLE recovery_case (
    id                 UUID PRIMARY KEY,
    display_id          TEXT NOT NULL UNIQUE,           -- e.g. 'RC-10281'
    payment_id         UUID NOT NULL REFERENCES payment(id),
    merchant_id        UUID NOT NULL REFERENCES merchant(id),
    customer_id        TEXT NOT NULL REFERENCES customer(customer_id),
    status             TEXT NOT NULL DEFAULT 'OPEN',
                                   -- state machine, see data/data-model.md:
                                   -- OPEN, ANALYZING, ACTION_SELECTED, ACTION_EXECUTED,
                                   -- WAITING_FOR_OUTCOME, and terminal RECOVERED / STOPPED /
                                   -- EXPIRED / FAILED. Phase 1A confirms final spelling.
    opened_at          TIMESTAMPTZ NOT NULL,            -- business: when the case opened
    closed_at          TIMESTAMPTZ,                     -- business: when it reached a terminal state (was 'resolved_at')
    last_evaluated_at  TIMESTAMPTZ,                     -- last pass through ANALYZING
    expires_at         TIMESTAMPTZ NOT NULL,            -- recovery window close → drives EXPIRED
    -- case context (carried from earlier drafts):
    amount_at_risk     NUMERIC NOT NULL,
    failure_category   TEXT,       -- TIMEOUT, INSUFFICIENT_FUNDS, AUTH_FAILURE, RISK_BLOCK, ABANDONED, OTHER
    failure_code       TEXT,
    experiment_arm     TEXT,       -- SUPERSEDED (Phase 1A.3 / ADR-011) by experiment_assignment
                                   -- (CONTROL / TREATMENT), which is now the authoritative
                                   -- assignment record — see data/data-model.md. Whether this
                                   -- legacy column is dropped or kept as a denormalized mirror
                                   -- is a Phase 1B decision; it must not be a second source of truth.
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    -- BUSINESS RULE (Phase 1B picks the constraint/partial index):
    -- at most one ACTIVE recovery_case per payment_id (active = status not terminal).
    -- NOT a permanent UNIQUE(payment_id).
);

CREATE TABLE recovery_case_status_history (
    id           BIGSERIAL PRIMARY KEY,
    case_id      UUID NOT NULL REFERENCES recovery_case(id),
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    reason       TEXT,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 1A.2 (ADR-010): a Prediction is PER DECISION CYCLE and PER
-- CANDIDATE ACTION (RETRY | MESSAGE | NO_ACTION), each bound to the exact
-- model_version that produced it. The single-row/action_probabilities-blob
-- shape below is superseded by the per-action shape (Phase 1B picks the
-- physical layout; the logical unit is per-action).
CREATE TABLE model_prediction (
    prediction_id         TEXT PRIMARY KEY,
    decision_record_id    UUID NOT NULL REFERENCES decision_record(id),   -- owning cycle
    case_id               UUID NOT NULL REFERENCES recovery_case(id),
    action                TEXT NOT NULL,   -- RETRY | MESSAGE | NO_ACTION (NO_ACTION = baseline)
    recovery_probability  NUMERIC NOT NULL,
    model_version_id      UUID NOT NULL REFERENCES model_version(id),     -- EXACT immutable version
    feature_snapshot      JSONB NOT NULL,  -- immutable inputs — see data-flow.md, privacy-architecture.md
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 1A.2: policy authorization is a DISTINCT record, one per candidate
-- action the veto loop checked. Recommendation ≠ policy result.
CREATE TABLE policy_evaluation (
    id                 UUID PRIMARY KEY,
    decision_record_id UUID NOT NULL REFERENCES decision_record(id),
    action             TEXT NOT NULL,      -- candidate action evaluated
    policy_id          TEXT NOT NULL,      -- which merchant policy
    policy_version     TEXT NOT NULL,      -- which version of it
    result             TEXT NOT NULL,      -- ALLOWED | BLOCKED
    reason_code        TEXT,               -- MAX_RETRY_LIMIT | MAX_CONTACTS | CHANNEL_DISABLED
                                           -- | NO_CONSENT | AMOUNT_LIMIT | RISK_FLAG | ...
    reason             TEXT,               -- human-readable detail
    evaluated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 1A.2: an Intervention is an action ACTUALLY ATTEMPTED/EXECUTED.
-- Created ONLY when final_action ∈ {RETRY, MESSAGE}. final_action =
-- NO_ACTION ⇒ NO intervention row.
CREATE TABLE intervention (
    intervention_id   TEXT PRIMARY KEY,
    decision_record_id UUID NOT NULL REFERENCES decision_record(id),  -- 0..1 per DecisionRecord
    case_id           UUID NOT NULL REFERENCES recovery_case(id),
    action            TEXT NOT NULL,   -- RETRY | MESSAGE (NOT NO_ACTION).
                                       -- Post-MVP: VOICE, and WHATSAPP/SMS/EMAIL as MESSAGE channels.
    channel           TEXT,            -- for MESSAGE: SIMULATED for MVP; WHATSAPP/SMS/EMAIL post-MVP
    execution_status  TEXT NOT NULL,   -- REQUESTED | ACCEPTED | REJECTED | FAILED
                                       -- (NO 'SUCCEEDED' — recovery success is an Outcome question)
    provider_ref      TEXT,            -- external reference for audit/idempotency
    cost_incurred     NUMERIC NOT NULL DEFAULT 0,   -- simulated/configured cost actually applied
    requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at       TIMESTAMPTZ
    -- policy result is NOT stored here — see policy_evaluation / decision_record.
);

-- Phase 1A.2: Outcome = what happened to the PAYMENT after this decision
-- cycle. Distinct from execution_status AND from recovery_case.status.
-- Attaches to the DecisionRecord (so NO_ACTION cycles can have one);
-- optionally references the Intervention it followed. Delayed outcomes OK.
CREATE TABLE outcome (
    outcome_id         TEXT PRIMARY KEY,
    decision_record_id UUID NOT NULL REFERENCES decision_record(id),
    intervention_id    TEXT REFERENCES intervention(intervention_id),   -- nullable (NO_ACTION cycle)
    result             TEXT NOT NULL,     -- RECOVERED | NOT_RECOVERED  (binary — matches ml/labels.md)
    recovery_amount    NUMERIC NOT NULL DEFAULT 0,
    observed_at        TIMESTAMPTZ NOT NULL,   -- when recovery/non-recovery was actually observed (may lag)
    recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### New concepts pending Phase 1A finalization

The following come from ADR-008 (Phase 0.6). The sketches below are
**non-binding conceptual placeholders** — Phase 1A finalizes exact
primary keys, foreign keys, nullability, indexes, and SQL types. Do not
implement these as-is.

```sql
-- Phase 1A.3 (ADR-011): identifies the EXACT, IMMUTABLE model responsible
-- for a Prediction. Immutable except `status`. Exactly one PROMOTED row
-- per model_role at a time (enforced how = Phase 1B; e.g. partial unique
-- index on (model_role) WHERE status = 'PROMOTED').
-- sketch only (Phase 1B fixes types/keys):
--   id, model_role (e.g. 'recovery_prediction'), algorithm,
--   artifact_ref, artifact_checksum,
--   training_dataset_snapshot_id (Phase 1A.4: identity of the exact set of
--     training_example rows used — immutable, reproducible content/hash
--     identity; no dataset registry built),
--   feature_schema_id (the feature schema this model expects —
--     Prediction -> ModelVersion -> feature schema, NOT a
--     DecisionRecord.feature_schema_version column),
--   training_config (jsonb), training_pipeline_version, random_seed,
--   evaluation_summary (jsonb),
--   status  DRAFT | VALIDATED | PROMOTED | RETIRED | REJECTED
--     (the ONLY mutable field; all else immutable — a materially
--      different model is always a NEW row, never an edit),
--   created_at, status_changed_at
-- Allowed transitions: DRAFT->VALIDATED, DRAFT->REJECTED,
--   VALIDATED->PROMOTED, VALIDATED->REJECTED, PROMOTED->RETIRED.
-- Forbidden: REJECTED->PROMOTED, RETIRED->PROMOTED, DRAFT->PROMOTED
--   (must pass through VALIDATED).
CREATE TABLE model_version ( ... Phase 1B ... );

-- The auditable record of one evaluate -> decide cycle (see
-- data/data-model.md "Decision data contract — Phase 1A.2"). Exists
-- whether or not anything was executed. Structured, not one JSON blob.
-- sketch only (Phase 1B fixes types/keys):
--   id, recovery_case_id, cycle_number, decision_timestamp,
--   payment_amount_at_decision,
--   recommended_action, final_action (stored SEPARATELY), decision_reason,
--   policy_version_ref, decision_engine_version (optional metadata), status
--   NO experiment_assignment reference here — assignment lives on
--   recovery_case ONLY (Phase 1A.3 / ADR-011); read it via recovery_case_id.
--   value context per candidate action: { action, cost_used, eirv_value }
--     (recovery_probability lives on the related model_prediction row)
--   links: model_prediction (1..N), policy_evaluation (1..N),
--          intervention (0..1), outcome (0..1)
--   model_version is DERIVED from the model_prediction rows (not a column).
--   feature_schema_version is DERIVED (pinned by model_version) — not stored.
CREATE TABLE decision_record ( ... Phase 1B ... );

-- Phase 1A.3: an experiment definition (what is being compared).
-- sketch only:
--   id, name, description, started_at, ended_at (nullable), status
CREATE TABLE experiment ( ... Phase 1B ... );

-- Phase 1A.3 (ADR-011): minimal CONTROL / TREATMENT labelling for
-- incremental-effect evaluation (see data/data-model.md
-- "ExperimentAssignment"). SUPERSEDES recovery_case.experiment_arm as the
-- authoritative assignment record.
-- CARDINALITY: one row per recovery_case (assignment is CASE-LEVEL ONLY —
-- NOT per decision_record; every decision_record under the case inherits
-- this arm by reading it via recovery_case_id). IMMUTABLE once assigned —
-- no UPDATE of `arm` for an existing row.
-- sketch only:
--   id, experiment_id, recovery_case_id (UNIQUE — one assignment per case),
--   arm (CONTROL|TREATMENT),
--   experimental_config_ref (nullable — e.g. references a model_version.id
--     for a model experiment; a REFERENCE, not a duplicated copy of
--     ModelVersion data), assigned_at
CREATE TABLE experiment_assignment ( ... Phase 1B ... );

-- Phase 1A.4 (ADR-012): a DERIVED, IMMUTABLE ML observation.
-- LOGICAL UNIT: one row per (decision_record, candidate action). The
-- physical shape (one row per action vs. a compact per-decision_record
-- layout with an `action` column) is Phase 1B.
-- KEY RULE: a Prediction is NOT an observed outcome. Only the OBSERVED
-- action carries an outcome label — never write labels for the other
-- candidate actions (no manufactured counterfactuals).
-- sketch only:
--   id, decision_record_id, recovery_case_id (GROUPING KEY for case-level
--     train/val/test splitting — all rows of a case go in one split),
--   action (RETRY|MESSAGE|NO_ACTION — the treatment feature),
--   observed_action (what actually happened this cycle — derived, NOT the
--     recommendation; final_action=NO_ACTION => observed_action=NO_ACTION,
--     no Intervention required),
--   is_observed (bool — action == observed_action AND outcome usable),
--   feature_snapshot (features AS OF the decision_record — from the
--     matching model_prediction row; NO post-decision data => no leakage),
--   outcome_label (RECOVERED|NOT_RECOVERED — ONLY when is_observed; else NULL),
--   recovery_amount (from outcome, when is_observed & RECOVERED),
--   observation_timestamp (outcome.observed_at — may lag the decision),
--   experiment_arm (INHERITED from recovery_case.experiment_assignment —
--     case-level; NOT a decision_record field),
--   model_version_id (via the cycle's model_prediction rows),
--   created_at
-- FAILED EXECUTION: final_action RETRY/MESSAGE but execution_status
--   REJECTED/FAILED => is_observed = false for that treatment (decision to
--   execute != execution success != recovery outcome). No causal-censoring
--   machinery for the MVP.
CREATE TABLE training_example ( ... Phase 1B / ML implementation ... );
```

Phase 1A.2 (ADR-010) resolved the version-traceability question: only
`Prediction → exact model_version` and `DecisionRecord → policy_version`
are required to reconstruct a historical decision. `feature_schema_version`
is **derived** (pinned by the model version; inputs captured in the
`feature_snapshot`) and is not stored on `decision_record`.
`decision_engine_version` is **optional debugging metadata** on
`decision_record`, not load-bearing (the EIRV formula is fixed and its
inputs/outputs are persisted).

Phase 1A.3 (ADR-011) resolved: `model_version` is immutable except
`status`, with exactly one `PROMOTED` row per `model_role`; `policy` rows
are immutable per `policy_version` (a change is a new row, not an edit);
`experiment_assignment` is one row per `recovery_case` (never per
`decision_record`), immutable once written, and is the sole authority for
`CONTROL`/`TREATMENT` — `recovery_case.experiment_arm` is superseded.

Phase 1A.4 (ADR-012) resolved: `training_example` is a **derived,
immutable** ML observation, logical unit = one per `(decision_record,
candidate action)`; only the `observed_action` carries an `outcome_label`
(no counterfactual labels); `NO_ACTION` rows need no `intervention`;
`recovery_case_id` is the grouping key for **case-level** train/val/test
splitting; the `feature_snapshot` is frozen as of the `decision_record`
(no future data); the training set for a `model_version` is a reproducible
`training_dataset_snapshot`.

## 3b. Phase 1B — implementation choices (IMPLEMENTED)

The schema above is realized in code. Concrete decisions made while
implementing (contract unchanged):

- **Models:** SQLAlchemy 2.0 ORM in `backend/models/` (`core_entities.py`,
  `governance.py`, `decision.py`, `training.py`) + `enums.py` for the
  controlled vocabularies. One `Base.metadata`.
- **Migration:** a single Alembic revision `0001_initial_schema` that runs
  `Base.metadata.create_all` (guarantees model↔migration parity; future
  migrations use `--autogenerate`). Runs against a clean PostgreSQL from
  `docker compose` (backend entrypoint) and from `alembic upgrade head`.
- **Identity:** `id` = `GUID` type decorator (native `uuid` on PostgreSQL,
  `CHAR(36)` on SQLite for fast dependency-free unit tests); `display_id`
  generated `M-/P-/RC-<00001>` from a `display_id_sequence` counter table.
- **Money:** `NUMERIC(18, 4)`, `asdecimal=True` — exact `Decimal` in Python.
- **JSON:** `JSONB` on PostgreSQL, `JSON` elsewhere (feature snapshots,
  `value_context`, `metadata`, policy array/dict rule fields).
- **Enums:** stored as `TEXT` + `CHECK (col IN (...))` (not native PG
  `ENUM`) so adding a future post-MVP value is a data change, not a type
  migration.
- **DB-level invariants** (partial unique indexes, PostgreSQL + SQLite):
  `uq_recovery_case_active_payment` (at most one non-terminal case per
  payment), `uq_model_version_promoted_per_role` (one `PROMOTED` per
  `model_role`), `uq_policy_active_per_merchant` (one active policy
  version per merchant). Lifecycle-transition rules and the
  NO_ACTION-has-no-Intervention / label-only-on-observed-action rules that
  the DB can't express are enforced in the repository layer + tested.
- **`recovery_case.experiment_arm`: DROPPED.** `experiment_assignment`
  (case-level, one row per case, immutable `arm`) is the sole authority
  (Phase 1A.3). No denormalized mirror.
- **`training_example` physical shape:** one row per
  `(decision_record, candidate action)` (the simplest realization of the
  logical unit). Derivation lives in
  `backend/repositories/training.py::generate_for_decision_record`.
- **Naming:** deterministic constraint/index names via a metadata naming
  convention (`pk_/fk_/uq_/ck_/ix_`).
- **`training_dataset_snapshot_id`:** a deterministic content hash
  (`tds-<n>-<sha256[:16]>`) of the row set — no dataset registry.
- **Repositories:** `backend/repositories/` — use-case-shaped
  (`MerchantRepository`, `PaymentEventRepository` [append-only, no
  update/delete], `RecoveryCaseRepository` [transition + status history],
  `DecisionCycleRepository`, `ModelVersionRepository` [lifecycle guard],
  `PolicyRepository`, `ExperimentRepository`, `TrainingExampleRepository`).
- **Schemas:** Pydantic read/create models in `backend/schemas/`, ORM-free,
  audit-safe fields only.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Store `feature_snapshot` as normalized columns | Would require a migration every time a feature is added/changed during rapid ML iteration; `jsonb` keeps the audit trail intact while features evolve (see `architecture/data-flow.md`). |
| Store customer PII fields directly on `customer` | Violates the privacy architecture's data classification; deliberately omitted (see comment in DDL above). |
| UUID-only primary keys with no human-readable code | **Resolved (ADR-009):** use both — an opaque internal `id` (UUID) for keys/FKs and a separate unique `display_id` (`RC-10281`, `M-019`) for humans. UUID-only loses debug/demo readability; human-readable-only leaks ordering/volume and couples keys to a display choice. |
| Human-readable `TEXT` primary keys (the earlier sketch) | Superseded by the `id` + `display_id` split above for the Phase 1A.1 core tables. |

## 5. Why this option

This schema is the direct, literal translation of the approved architecture
and lifecycle — every table maps to exactly one entity from
`data/data-model.md`, and the `feature_snapshot`/status-history design
directly implements the audit-trail requirement from
`architecture/security-and-safety.md`.

## 6. Example

See `architecture/decision-flow.md` section 6 for how `RC-10281` populates
these tables end-to-end.

## 7. Implementation implications

- Actual implementation (Phase 1B/6) should use SQLAlchemy models in
  `backend/models/` mirroring this DDL, with Pydantic schemas in
  `backend/schemas/` for API I/O — only after Phase 1A finalizes the data
  contract (see `docs/README.md` and ADR-007).
- Indexes to add at implementation time (not detailed above): on
  `recovery_case.status`, `recovery_case.merchant_id`,
  `payment_event.payment_id`, `decision_record.recovery_case_id`,
  `model_prediction.decision_record_id`,
  `policy_evaluation.decision_record_id`,
  `intervention.decision_record_id`, `outcome.decision_record_id`,
  `training_example.recovery_case_id` (case-level splitting),
  `training_example.decision_record_id`.
- The `model_prediction` sketch above references `decision_record` and
  `model_version` (both defined further down as Phase 1B sketches) — the
  ordering is illustrative only.

## 8. Open questions

- **Resolved (Phase 1A.1 / ADR-009):** primary-key strategy is UUID `id` +
  human-readable `display_id` for the core tables. The same treatment is
  extended to `customer`, `policy`, and the ADR-008 concept tables in
  later Phase 1A steps.
- **Resolved (Phase 1A.3 / ADR-011):** `model_version` lifecycle/
  immutability/one-promoted-per-role; `policy` version immutability;
  `experiment_assignment` cardinality (one per `recovery_case`) and
  immutability; `recovery_case.experiment_arm` superseded.
- **Resolved (Phase 1A.4 / ADR-012):** `training_example` logical unit
  (per `decision_record` × candidate action), observed-action rule,
  label-only-for-observed-action, `NO_ACTION`-without-intervention,
  case-level splitting via `recovery_case_id`, leakage rule, reproducible
  dataset snapshot.
- Phase 1B decides: exact numeric precision/scale for money, the
  timestamp type, the partial-index/constraint that enforces "at most one
  active `recovery_case` per payment" and "exactly one `PROMOTED`
  `model_version` per `model_role`", enum vs `TEXT`+CHECK for status
  fields, the physical `training_example` shape (per-action rows vs
  compact), the exact `training_dataset_snapshot_id` hash/storage
  mechanism, split ratios/seed, the physical representation of `policy`
  rules (columns vs `jsonb`), and all indexes.

## 9. Visual

See the entity relationship diagram in `data/data-model.md` section 9 —
this document adds field-level detail to that same shape.
