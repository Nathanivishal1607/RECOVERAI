# Data Model

## 1. Purpose

Define the core entities, their relationships, and — most importantly — the
`RecoveryCase` lifecycle, which is the central object the entire system
revolves around.

## 2. Context

Every architectural document references these entities. This document is
their authoritative definition; `data/database-schema.md` translates it into
actual table DDL.

## 3. Current decision

### Entities

```
Merchant
Customer
Payment
PaymentEvent
RecoveryCase        ← central business/audit object
ModelVersion        ← identifies the model that produced a prediction/decision
Prediction          ← "what is likely to happen" (a.k.a. ModelPrediction)
DecisionRecord      ← "what did the system decide, and why" (auditable)
Intervention        ← "what the system actually executed"
Outcome             ← "what happened afterward"
Policy
Experiment
ExperimentAssignment ← which arm (CONTROL / TREATMENT) a case is in
PolicyEvaluation     ← per-candidate policy result on a DecisionRecord
TrainingExample      ← one ML observation = one DecisionRecord × one candidate action
```

> Naming: `Prediction` and `ModelPrediction` refer to the same entity; the
> DDL table is `model_prediction`.
>
> **Phase 1A.1** finalizes the *core data contract* for the four
> foundational entities — `Merchant`, `Payment`, `PaymentEvent`,
> `RecoveryCase` — in the "Core data contract" section below (see
> ADR-009). **Phase 1A.2** finalizes the *decision data contract* —
> `Prediction`, `DecisionRecord`, `PolicyEvaluation`, `Intervention`,
> `Outcome` — in the "Decision data contract" section below (see ADR-010).
> **Phase 1A.3** finalizes the *model, policy & experiment data contract* —
> `ModelVersion`, `Policy`, `Experiment`, `ExperimentAssignment` — in the
> "Model, Policy & Experiment data contract" section below (see ADR-011).
> **Phase 1A.4** finalizes the *training data contract* — `TrainingExample`
> — in the "Training data contract" section below (see ADR-012). This
> completes the Phase 1A data contract.

### Relationships

```
Merchant 1──* Customer
Merchant 1──* Payment
Merchant 1──* Policy version   (each version immutable; exactly one is "current active" — see Phase 1A.3)
Customer 1──* Payment
Payment  1──* PaymentEvent
Payment  1──0..1 RecoveryCase   (at most one ACTIVE case per payment;
                                 MVP simplification — see note below)

RecoveryCase 1──* DecisionRecord    (one per evaluate→decide cycle; >1 if the case re-evaluates;
                                     NEVER overwritten — see "Multiple decision cycles")

DecisionRecord 1──* Prediction        (one per candidate action — RETRY, MESSAGE, NO_ACTION;
                                       Predictions belong to exactly one DecisionRecord)
DecisionRecord 1──* PolicyEvaluation  (one per candidate action that was policy-checked)
DecisionRecord 1──0..1 Intervention   (only if final_action ∈ {RETRY, MESSAGE};
                                       final_action = NO_ACTION ⇒ no Intervention)
DecisionRecord 1──0..1 Outcome        (the observed result of this decision cycle, when resolved)

Outcome 0..1──0..1 Intervention       (an Outcome may reference the Intervention it followed;
                                       a NO_ACTION cycle's Outcome has no Intervention)

Prediction *──1 ModelVersion          (the EXACT immutable model version that produced it —
                                       authoritative; a DecisionRecord's model reference is
                                       derived from its Predictions)

RecoveryCase 1──0..1 ExperimentAssignment   (Phase 1A.3: assignment lives at the RecoveryCase
                                       level ONLY, immutable; every DecisionRecord under the
                                       case inherits — not stored as a per-DecisionRecord relation)
Experiment 1──* ExperimentAssignment

DecisionRecord 1──* TrainingExample   (Phase 1A.4: one per candidate action per cycle
                                       (RETRY/MESSAGE/NO_ACTION); derived, immutable; only the
                                       observed action carries an outcome label)
TrainingExample *──1 RecoveryCase     (grouping key for leakage-safe train/val/test splitting)
TrainingExample *──1 ModelVersion     (via the cycle's Predictions)
```

Every `Prediction` carries the `ModelVersion` that produced it, so any
historical decision can be traced back to the exact immutable model — see
"Model-version traceability" below.

#### `Payment → RecoveryCase` is an MVP simplification

For the MVP, a failed `Payment` maps to **at most one active**
`RecoveryCase` — and only if the failure passes recovery eligibility (see
"Recovery eligibility" below; a failed payment does not automatically
create a case). This is a deliberate simplification, not a permanent
constraint, and the data model is **not** being redesigned around it. The
"at most one active" rule is a business rule, not a permanent
`UNIQUE(payment_id)` — a post-MVP recovery episode could open a later case
for the same payment once the first has closed.

The underlying payment lifecycle already supports multiple attempts/events
through `PaymentEvent`, and that is where multi-attempt history lives:

```
Payment
   │
   ├── PaymentEvent: attempt 1 failed
   ├── PaymentEvent: retry attempted
   ├── PaymentEvent: attempt 2 failed
   └── PaymentEvent: attempt 3 succeeded
```

> **`PaymentEvent` is the authoritative chronological record of payment
> lifecycle events.** `Payment.status` is a convenience denormalization of
> the latest known state; the ordered `PaymentEvent` stream is the source
> of truth.

We are deliberately **not** introducing an `Order → Payment →
PaymentAttempt` hierarchy unless a later phase actually requires it. The
lean structure stays.

### Core data contract — Phase 1A.1 (Merchant, Payment, PaymentEvent, RecoveryCase)

This section is the **conceptual + logical** contract for the four
foundational entities. It fixes entity purpose, identity, lifecycle,
relationships, field semantics, and business rules. It does **not** fix
PostgreSQL types, indexes, foreign-key mechanics, or constraint syntax —
those are Phase 1B (see "Data contract vs. implementation" at the end of
this section, and ADR-009).

#### Shared conventions

- **Identity:** every top-level business entity has an internal primary
  identity `id` (opaque UUID, never provider-derived) and a separate
  human-readable `display_id` (e.g. `M-019`, `P-78291`, `RC-10281`) for
  debugging, dashboards, and demo narration. `display_id` is unique per
  entity type. Application logic and foreign keys use `id`; humans use
  `display_id`. (`PaymentEvent` is an internal append-only record and has
  only `id`.)
- **Timestamps:** `created_at` / `updated_at` are *system* audit
  timestamps (when our system wrote/changed the row). Business-lifecycle
  timestamps (`opened_at`, `closed_at`, `event_timestamp`, …) are named
  separately and may differ from `created_at`.
- **Money:** monetary amounts are exact decimal values with an explicit
  `currency`. Never floating-point. (The DB representation is Phase 1B.)
- **Provider data:** external identifiers and payloads live in dedicated
  fields (`external_payment_id`, `provider_event_id`, `metadata`) — they
  never replace an internal `id`, and provider status vocabularies are
  *mapped* to our internal vocabularies, not passed through raw.
- **Privacy:** card numbers, CVV, UPI PIN, bank credentials, API/auth
  secrets, and unnecessary PII are **not** part of this data model (see
  `architecture/privacy-architecture.md`).

#### Merchant

Purpose: the merchant using RecoverAI; owns payments, policy, customers,
and recovery cases.

| Field | Semantics | Req? |
|---|---|---|
| `id` | internal UUID PK | required |
| `display_id` | human-readable unique id (`M-019`) | required |
| `name` | merchant display name | required |
| `status` | `ACTIVE` \| `INACTIVE` | required, default `ACTIVE` |
| `created_at`, `updated_at` | system timestamps | required |

- Lifecycle: `ACTIVE ↔ INACTIVE`. An `INACTIVE` merchant's payments are
  still ingested for history but are **not recovery-eligible**. Two states
  are sufficient for the MVP — no `SUSPENDED` / `ONBOARDING` unless a
  concrete need appears.
- Relationships: `Merchant 1──* Payment`, `Merchant 1──* Customer`,
  `Merchant 1──* RecoveryCase`, `Merchant 1──1 active Policy` (history
  retained).
- Uniqueness: `id` unique; `display_id` unique.
- Existing sketch fields `industry`, `currency` (default) are optional
  context, not part of the core contract.

#### Payment

Purpose: the internal representation of a payment / revenue opportunity.
It is **not** a mirror of the provider's payment object — it is our lean
lifecycle view of one.

| Field | Semantics | Req? |
|---|---|---|
| `id` | internal UUID PK | required |
| `display_id` | human-readable unique id (`P-78291`) | required |
| `merchant_id` | owning merchant (`Merchant.id`) | required |
| `external_payment_id` | provider/Razorpay payment id | optional (once known) |
| `amount` | exact decimal | required |
| `currency` | ISO 4217 (`INR`, …) — always explicit | required |
| `status` | internal status vocabulary (below) | required |
| `created_at`, `updated_at` | system timestamps | required |

- **Internal status vocabulary (MVP):**

  ```
  CREATED     — payment initiated, no terminal result yet
  PROCESSING  — an attempt is in flight (auth / pending / etc.)
  FAILED      — the latest attempt failed (the recovery trigger)
  SUCCEEDED   — payment ultimately succeeded (terminal success)
  CANCELLED   — abandoned / cancelled, no further attempts expected
  ```

  This is intentionally small and **sufficient** for the recovery MVP.
  Earlier schema drafts also listed `authorized`, `captured`, `refunded`;
  the recovery decision needs none of them as distinct internal states
  (`authorized` → `PROCESSING`, `captured` → `SUCCEEDED`, `refunded` is a
  post-success reversal, out of MVP recovery scope). No state is added
  merely for completeness.

- **Provider → internal status mapping** (illustrative; exact Razorpay
  values confirmed in Phase 8):

  | Provider signal (examples) | Internal `status` |
  |---|---|
  | `created`, order created | `CREATED` |
  | `authorized`, `pending`, in-flight | `PROCESSING` |
  | `failed`, `error` | `FAILED` |
  | `captured`, `paid` | `SUCCEEDED` |
  | `cancelled`, `voided` | `CANCELLED` |
  | `refunded` | *(not represented in the MVP payment model)* |

- `Payment.status` is a **denormalized convenience** — the authoritative
  history is the ordered `PaymentEvent` stream. If they could disagree,
  the events win.
- Identity: `external_payment_id` never becomes the PK and may be absent
  for synthetic/simulated payments; `id` is always our UUID.
- Relationships: `Payment 1──* PaymentEvent`; `Payment 1──0..1 active
  RecoveryCase` (see uniqueness rule under RecoveryCase).

#### PaymentEvent

Purpose: the **immutable, append-only, chronological** record of what
happened to a payment. It is the authoritative payment lifecycle source;
`Payment.status` is derived from it.

| Field | Semantics | Req? |
|---|---|---|
| `id` | internal UUID PK | required |
| `payment_id` | owning payment (`Payment.id`) | required |
| `event_type` | controlled vocabulary (below) | required |
| `event_timestamp` | when the event *occurred* (provider/world time) | required |
| `created_at` | when *our system* recorded/ingested it | required |
| `attempt_number` | which payment attempt this belongs to; **nullable** | optional |
| `amount`, `currency` | amount in play for this event (usually = payment) | optional |
| `provider_event_id` | external event id, for idempotency / audit | optional |
| `metadata` | safe provider payload / error codes / context | optional |

- **Controlled MVP event vocabulary** (smallest set that supports the
  recovery use case):

  ```
  PAYMENT_CREATED     — payment initiated
  PAYMENT_FAILED      — an attempt failed  (the recovery trigger)
  RETRY_ATTEMPTED     — a new attempt was initiated (by the customer or by RecoverAI)
  PAYMENT_SUCCEEDED   — payment ultimately succeeded (closes recovery as RECOVERED)
  PAYMENT_CANCELLED   — abandoned / cancelled, no further attempts expected
  ```

  Rationale for excluding the other candidates: `PAYMENT_PROCESSING` /
  `PAYMENT_AUTHORIZED` are transient in-flight signals the recovery
  decision never acts on (they update `Payment.status` to `PROCESSING`
  and/or land in `metadata`); `PAYMENT_CAPTURED` is folded into
  `PAYMENT_SUCCEEDED` (one terminal-success event). New event types are
  added only when a concrete recovery / simulator / adapter need appears —
  not for parity with the provider's full event catalogue.

- **Provider webhook → internal event mapping** (illustrative; confirmed
  in Phase 8 — see `data/events.md`):

  | Razorpay webhook (examples) | Internal `event_type` |
  |---|---|
  | `payment.failed` | `PAYMENT_FAILED` |
  | `payment.captured`, `order.paid` | `PAYMENT_SUCCEEDED` |
  | a retry we trigger, or `payment.created` on a re-attempt | `RETRY_ATTEMPTED` |
  | `payment.created` (first attempt) | `PAYMENT_CREATED` |
  | `payment.authorized` / pending | *(status → `PROCESSING`; no distinct event required in MVP)* |
  | payment voided / cancelled | `PAYMENT_CANCELLED` |

- **`attempt_number` semantics:** set on events that belong to a specific
  attempt (`PAYMENT_FAILED`, `RETRY_ATTEMPTED`, `PAYMENT_SUCCEEDED`);
  `NULL` for events that don't (`PAYMENT_CREATED` before any attempt, some
  administrative events). The simulator, backend, and any future provider
  adapter must assign `attempt_number` the same way.
- **Immutability:** events are never updated or deleted. A correction is a
  new event, not an overwrite.
- **`event_timestamp` vs `created_at`:** an event can be ingested later
  than it occurred (webhook delay, backfill). Chronology uses
  `event_timestamp`; audit of our ingestion uses `created_at`.

Worked example — one payment, many events:

```
Payment P-78291
  PAYMENT_CREATED     attempt = NULL
  PAYMENT_FAILED      attempt = 1
  RETRY_ATTEMPTED     attempt = 2
  PAYMENT_FAILED      attempt = 2
  PAYMENT_SUCCEEDED   attempt = 3
```

#### RecoveryCase (core fields)

Purpose: the central business/audit object for one recovery journey. Not
created automatically for every failed payment — see "Recovery
eligibility".

| Field | Semantics | Req? |
|---|---|---|
| `id` | internal UUID PK | required |
| `display_id` | human-readable unique id (`RC-10281`) | required |
| `merchant_id` | owning merchant (`Merchant.id`) | required |
| `payment_id` | the payment under recovery (`Payment.id`) | required |
| `status` | RecoveryCase state machine (see next section) | required, initial `OPEN` |
| `opened_at` | when the case was opened (business time) | required |
| `closed_at` | when the case reached a terminal state | set on close |
| `last_evaluated_at` | last time the case ran through `ANALYZING` | set per eval |
| `expires_at` | when the recovery window closes (drives `EXPIRED`) | required |
| `created_at`, `updated_at` | system timestamps | required |

- Existing sketch fields `customer_id`, `amount_at_risk`,
  `failure_category`, `failure_code` carry over as case context;
  `resolved_at` in the older sketch is **superseded by** `closed_at`.
- Relationships (MVP):

  ```
  Merchant 1 ──── N RecoveryCase
  Payment  1 ──── 0..1 ACTIVE RecoveryCase
  ```

- **Uniqueness rule (business, not SQL):** *at most one **active**
  RecoveryCase per payment*, where "active" = status not terminal. This is
  **not** a permanent `UNIQUE(payment_id)`. Phase 1B chooses the
  constraint / partial index that enforces "at most one active" without
  forbidding a later (post-MVP) recovery episode for the same payment.
- No `Order`, `PaymentAttempt`, or `RecoveryEpisode` hierarchy is
  introduced (the Phase 0.6 decision stands).

#### Recovery eligibility (a distinct stage)

A `PAYMENT_FAILED` event does **not** create a RecoveryCase by itself. It
triggers an **eligibility evaluation**:

```
PaymentEvent (PAYMENT_FAILED)
        ↓
Recovery eligibility check
        ↓
 ┌────────────┴────────────┐
 ▼                          ▼
Eligible                  Ineligible
 ▼                          ▼
open RecoveryCase          no case (logged)
```

Eligibility asks *"should this payment enter the recovery system at
all?"* — a gate, not an optimisation. Illustrative considerations (the
full rule set is finalised in the decision-engine phase, **not** here):

```
payment status is FAILED
AND payment belongs to a supported, ACTIVE merchant
AND amount is within a recoverable range
AND no active RecoveryCase already exists for this payment
AND the payment is within the recovery time window
AND merchant policy permits recovery for this payment
```

Eligibility is **separate from EIRV**:

| Stage | Question |
|---|---|
| Recovery eligibility | "Should this payment enter the recovery system?" |
| EIRV (decision engine) | "Given that a case exists and is being evaluated, which action has the greatest expected incremental value?" |

Do not put EIRV math into eligibility, and do not put eligibility gating
into EIRV. See `decision-engine/decision-engine.md`.

#### `NO_ACTION` vs terminal `STOPPED` (restated for this contract)

- `NO_ACTION` is a **decision/recommendation outcome**: "evaluated, no
  intervention right now." The case usually stays observable
  (`WAITING_FOR_OUTCOME`) and may re-evaluate; it is **not** terminal.
- `STOPPED` is a **terminal case status**: a hard stopping condition was
  reached (max retries, max contacts, opt-out, hard policy restriction,
  recovery window closed) and the case will not be revisited.

These are never collapsed. A case can record many `NO_ACTION` decisions
before (or without ever) reaching `STOPPED`.

#### Core relationship diagram

```
Merchant
   │ 1:N
   ▼
Payment
   │
   ├─────────────── 1:N ──────────────►  PaymentEvent   (chronological lifecycle source)
   │
   │ 0..1 active
   ▼
RecoveryCase                             (recovery business lifecycle)
```

`PaymentEvent` and `RecoveryCase` are different responsibilities:
`PaymentEvent` records *what happened to the money*; `RecoveryCase`
records *what RecoverAI decided and did about it*.

#### Data contract vs. implementation

Phase 1A.1 fixes: entities, relationships, business rules, lifecycle,
field semantics, required/optional concepts, identity strategy, and the
status / event vocabularies. Phase 1B fixes: SQLAlchemy models,
PostgreSQL types, foreign keys, indexes, constraints, migrations,
repositories, Pydantic schemas. Nothing in this section commits a column
type or an index.

### RecoveryCase — the central object

```
RecoveryCase:
  id:               <uuid>
  display_id:        RC-10281
  merchant_id:       <merchant uuid>       (display M-019)
  payment_id:        <payment uuid>        (display P-78291)
  status:            OPEN
  opened_at, closed_at, last_evaluated_at, expires_at
  created_at, updated_at
  -- case context (carried from earlier drafts):
  customer_id, amount_at_risk: ₹5,000, failure_category: TIMEOUT,
  failure_code: BAD_REQUEST_ERROR
```

See "Core data contract — Phase 1A.1" above for the authoritative field
semantics.

### RecoveryCase state machine

The lifecycle is an explicit state machine. Phase 1A confirms the final
state names and stores transitions in `recovery_case_status_history`
(append-only); this section defines the intended semantics.

#### States

| State | Meaning |
|---|---|
| `OPEN` | Case opened after a `PAYMENT_FAILED` event passed recovery eligibility (see "Recovery eligibility" above). Nothing evaluated yet. |
| `ANALYZING` | Features extracted; a `Prediction` is produced, EIRV computed, a recommendation formed, policy evaluated — i.e. a `DecisionRecord` is being assembled. Stopping rules are checked here. |
| `ACTION_SELECTED` | `DecisionRecord` finalized: recommendation + policy result + final action are fixed. Nothing executed yet. |
| `ACTION_EXECUTED` | The action gateway has settled `final_action`. If `final_action ∈ {RETRY, MESSAGE}`, the gateway made the provider call and an `Intervention` row exists (with its `execution_status`). If `final_action = NO_ACTION`, no provider call is made and **no `Intervention` row exists** — this state is reached as a no-op confirmation that the decision is settled. |
| `WAITING_FOR_OUTCOME` | Execution done; awaiting an observable result (`PAYMENT_SUCCEEDED`, or a timeout). |
| `RECOVERED` *(terminal)* | Payment captured within the resolution window. |
| `STOPPED` *(terminal)* | A stopping rule ended the case with no (further) intervention **and** no re-evaluation possible — e.g. retry/contact limits reached, customer opted out, or policy leaves no allowed action and the case will not be revisited. (A deliberate `NO_ACTION` decision that still waits to observe natural recovery is **not** `STOPPED` — it goes through `WAITING_FOR_OUTCOME`.) |
| `EXPIRED` *(terminal)* | The resolution time window elapsed with no recovery. |
| `FAILED` *(terminal)* | An unrecoverable system/execution error prevented a clean decision or outcome. |

`CLOSED` is not a distinct state — it is an umbrella term for "the case is
in a terminal state" (`RECOVERED` | `STOPPED` | `EXPIRED` | `FAILED`).
Existing prose that says "closed case" means exactly this.

#### Transitions

| From | Event / condition | To |
|---|---|---|
| `OPEN` | feature extraction + prediction begins | `ANALYZING` |
| `ANALYZING` | a `DecisionRecord` is finalized (final action may be `RETRY`, `MESSAGE`, or a deliberate `NO_ACTION`) | `ACTION_SELECTED` |
| `ANALYZING` | a hard stopping rule fires and the case will not be revisited (limits reached, opt-out, no allowed action) | `STOPPED` |
| `ANALYZING` | unrecoverable error building the decision | `FAILED` |
| `ACTION_SELECTED` | action gateway executes the final action | `ACTION_EXECUTED` |
| `ACTION_SELECTED` | execution rejected/errored unrecoverably | `FAILED` |
| `ACTION_EXECUTED` | any final action (incl. `NO_ACTION`) — now observe | `WAITING_FOR_OUTCOME` |
| `WAITING_FOR_OUTCOME` | `PAYMENT_SUCCEEDED` observed | `RECOVERED` |
| `WAITING_FOR_OUTCOME` | outcome known, not recovered, another round allowed | `ANALYZING` (re-evaluate) |
| `WAITING_FOR_OUTCOME` | outcome known, not recovered, a hard stopping rule now applies | `STOPPED` |
| `WAITING_FOR_OUTCOME` | resolution window elapsed with no recovery | `EXPIRED` |

The `WAITING_FOR_OUTCOME → ANALYZING` edge is the **re-evaluate loop**
(e.g. a retry failed, now consider a message). It is bounded by the
stopping rules in `architecture/security-and-safety.md`.

#### Terminal states

`RECOVERED`, `STOPPED`, `EXPIRED`, `FAILED`. A case in any of these makes
no further transitions and receives no further automated action.

#### Invalid transitions (the system must reject these)

```
RECOVERED  → ACTION_EXECUTED      (a terminal state never re-opens)
EXPIRED    → ANALYZING
STOPPED    → ACTION_SELECTED
OPEN       → ACTION_EXECUTED      (cannot skip evaluation + selection)
ACTION_SELECTED → RECOVERED       (must execute and observe an outcome first)
any terminal → any state
```

#### Relationship to other entities

| Entity | Relationship to the state machine |
|---|---|
| `PaymentEvent` | The payment's own chronological lifecycle. `PAYMENT_FAILED` triggers recovery eligibility, which (if eligible) drives `→ OPEN`; `PAYMENT_SUCCEEDED` drives `→ RECOVERED`. RecoveryCase state is RecoverAI's internal decision state, distinct from `Payment.status`. |
| `DecisionRecord` | One is assembled per `ANALYZING` pass; finalized at `→ ACTION_SELECTED` (or records the stop at `→ STOPPED`). |
| `Intervention` | Created on `ACTION_SELECTED → ACTION_EXECUTED` **only when** `final_action ∈ {RETRY, MESSAGE}`. A `final_action = NO_ACTION` still makes this transition (see the state table above) but creates **no** `Intervention` row. |
| `Outcome` | Recorded during `WAITING_FOR_OUTCOME`; determines `RECOVERED` vs re-evaluate (`→ ANALYZING`) vs `STOPPED` vs `EXPIRED`. |
| `StoppingRule` | Evaluated in `ANALYZING` / `WAITING_FOR_OUTCOME`; can route a case to `STOPPED` instead of another action. |

### RecoveryCase vs. TrainingExample

These are two different things and must not be conflated.

**RecoveryCase** is the central *business / audit* object — a
revenue-at-risk recovery journey:

```
RecoveryCase
 ├── Payment
 ├── Prediction(s)
 ├── Intervention(s)
 ├── Outcome(s)
 └── Audit events
```

**TrainingExample** is an *ML dataset* representation derived from observed
case / intervention / outcome data. It is a modelling artifact, not a
business record.

One RecoveryCase does **not** necessarily equal exactly one
TrainingExample. A single case can produce **multiple** observations
(one per decision cycle, per the granularity fixed in Phase 1A.4). The
full contract — granularity, the observed-action rule, eligibility,
leakage prevention, and case-level splitting — is in "Training data
contract — Phase 1A.4" below (see ADR-012).

### Prediction vs. Recommendation vs. Execution (intro)

These are distinct concepts and must **not** be collapsed into one generic
`action` field. Summary here; the finalized contract is in "Decision data
contract — Phase 1A.2" below.

| Concept | Question it answers | Example | Where it lives |
|---|---|---|---|
| **Prediction** | "What is likely to happen *under this specific action*?" | `P(recover \| RETRY)=0.72`, `P(recover \| MESSAGE)=0.61`, `P(recover \| NO_ACTION)=0.43` — one Prediction per candidate action | `Prediction` (per DecisionRecord, per action) |
| **EIRV** | "What is the expected incremental *economic value* of this action?" | `EIRV(RETRY) ≈ ₹...` — computed by the decision engine, not the model | `DecisionRecord` value context |
| **Recommendation** | "Given predictions + economics, which action *should* we take (pre-policy)?" | `recommended_action = RETRY` | `DecisionRecord.recommended_action` |
| **Policy evaluation** | "Is that action *authorized*?" | `RETRY → BLOCKED (MAX_RETRY_LIMIT)` | `PolicyEvaluation` (per candidate) |
| **Final action** | "What did the system decide to do?" | `final_action = NO_ACTION` (recommendation was blocked) | `DecisionRecord.final_action` |
| **Execution** | "What did the provider actually do with it?" | `Intervention(RETRY, execution_status = ACCEPTED)` | `Intervention` (only for `RETRY`/`MESSAGE`) |
| **Outcome** | "What happened to the payment afterward?" | `Outcome(NOT_RECOVERED)` — then a later cycle: `Outcome(RECOVERED, ₹1,000)` | `Outcome` (per decision cycle) |

The recommendation **can differ** from the final action, and the final
action **can differ** from what was executed successfully:

```
Recommendation: RETRY
Policy:         BLOCKED (MAX_RETRY_LIMIT)
Final action:   NO_ACTION
Intervention:   NULL
```

Conceptual flow (one decision cycle):

```
Features → Prediction(per action) → EIRV(per action) → Recommendation
        → Policy Evaluation(per candidate) → DecisionRecord(final_action)
        → Intervention (if RETRY/MESSAGE) → Execution status → Outcome
```

### DecisionRecord — the auditable unit of decision (intro)

RecoverAI's purpose is not to predict; it is to make a financially
meaningful **decision**. A `DecisionRecord` is the authoritative audit
representation of **one evaluate→decide cycle**. It links structured
records (predictions, policy evaluations, value context, intervention,
outcome) — it is **not** one giant JSON blob. Full field/relationship
contract: "Decision data contract — Phase 1A.2" below.

### ModelVersion — prediction/decision traceability

The data contract must let every historical decision be traced to the
exact model version behind it. **`Prediction` is the load-bearing
reference** (`model_version_id`, exact and immutable) — a `DecisionRecord`
does not independently store its own model-version column; its model
reference is *derived* from the `Prediction`s it links to (finalized in
"Decision data contract — Phase 1A.2" below and ADR-010):

```
ModelVersion → Prediction → (derives) DecisionRecord's model reference → Intervention → Outcome
```

```
Case A → model_version = uplift_v1.0 → MESSAGE → recovered
Case B → model_version = uplift_v2.0 → RETRY   → recovered
```

At minimum the eventual contract must support:

```
model_version_id
model_name
version
status          DRAFT | VALIDATED | PROMOTED | RETIRED | REJECTED
created_at
```

Finalized in Phase 1A.3 (see "Model, Policy & Experiment data contract"
below and ADR-011): `feature_schema_version` is carried **by
`ModelVersion`** (not ambiguous, not on `DecisionRecord`);
`decision_engine_version` remains optional debugging metadata on
`DecisionRecord`, not load-bearing; `policy_version` is required, but on
`DecisionRecord`/`PolicyEvaluation`, not on `ModelVersion`.

`ModelPrediction.model_version` (today a plain string) becomes a reference
to this concept.

### ExperimentAssignment — CONTROL vs TREATMENT (intro)

Because the product claims to optimize **incremental** recovery, the
design must be able to distinguish *"what percentage recovered?"* from
*"how much additional recovery did our intervention cause?"*. Beyond the
simulator's hidden ground truth, the system supports a minimal controlled
treatment/control split, assigned at the `RecoveryCase` level and kept
immutable — full contract, including precise `CONTROL`/`TREATMENT`
semantics (not "force an action" — see below), in "Model, Policy &
Experiment data contract — Phase 1A.3" and ADR-011.

```
CONTROL   → the existing/default strategy (comparison baseline)
TREATMENT → the experimental strategy under evaluation (may vary model
            version, decision strategy, or economic configuration — NOT
            a forced action; NO_ACTION stays available in both arms)
```

This **supersedes** the placeholder `recovery_case.experiment_arm` string
— `ExperimentAssignment` is the authoritative assignment record, not a
second source of truth. The exact experiment design, randomization
mechanism, allocation strategy, and statistical methodology are finalized
later in the ML/evaluation phase — this pass does **not** lock a
statistical method, and does **not** introduce an experimentation
platform.

> **Not the same as simulator ground truth.** The synthetic simulator
> (`data/synthetic-data.md`) *knows* hidden values (natural recovery
> probability, true per-action effects) and is used for offline
> evaluation. `ExperimentAssignment` is an *observational* mechanism:
> CONTROL vs TREATMENT groups whose realized outcomes are compared to
> estimate incremental effect without any hidden knowledge. Keep the two
> separate.

### Decision data contract — Phase 1A.2 (Prediction, DecisionRecord, PolicyEvaluation, Intervention, Outcome)

Conceptual + logical contract for the RecoverAI **decision lifecycle**. It
makes every financial recovery decision traceable, explainable,
reproducible, auditable, and usable by the future ML learning loop. It
does **not** fix SQL types, keys, indexes, or migrations (Phase 1B), and
it does not change the EIRV formula (ADR-003 stands).

#### The four lifecycles (keep them separate)

| Lifecycle | Records | Owns |
|---|---|---|
| **Payment** | `Payment` → `PaymentEvent(s)` | what happened to the money (authoritative, append-only) |
| **Recovery** | `RecoveryCase` → status-machine transitions | the recovery journey's overall state |
| **Decision** | `DecisionRecord` → `Prediction(s)` → EIRV → `Recommendation` → `PolicyEvaluation(s)` → `final_action` | one evaluate→decide cycle |
| **Action / Outcome** | `Intervention` → execution status → `Outcome` | one attempted recovery action and its observed result |

These layers interact but are **never collapsed**. In particular
`DecisionRecord` does **not** duplicate `PaymentEvent`s — payment
lifecycle events stay in `PaymentEvent` (ADR-009).

#### `Prediction`

> A model-generated estimate of the expected outcome under **one specific
> candidate action**, given the information available at that decision
> cycle.

- **Action-specific.** One `Prediction` per candidate action per
  `DecisionRecord`. For the MVP that is exactly three per cycle: `RETRY`,
  `MESSAGE`, `NO_ACTION` (the `NO_ACTION` prediction is the baseline /
  control estimate).
- Belongs to **exactly one** `DecisionRecord` (not shared across cycles —
  a later cycle re-predicts).
- References the **exact immutable `ModelVersion`** that produced it (not
  `model_name` alone).
- Carries the **feature snapshot** used (immutable — see
  `architecture/data-flow.md`), which doubles as the audit record of model
  inputs and the training-observation context.

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id |
| `decision_record_id` | owning decision cycle |
| `action` | `RETRY` \| `MESSAGE` \| `NO_ACTION` |
| `recovery_probability` | model estimate of P(recover \| action, context) |
| `model_version_id` | exact `ModelVersion` reference (required) |
| `feature_snapshot` | immutable inputs used |
| `created_at` | system timestamp |

`Prediction` is **not** EIRV and **not** a recommendation. The model
produces `Prediction`s; the decision engine turns them into EIRV and a
recommendation:

```
ML Model → action-specific Predictions → EIRV (economic calc) → Recommendation
```

#### `DecisionRecord`

> The authoritative audit representation of **one** evaluate→decide cycle.

Structured — it *links* records, it is not a single opaque JSON object.
Anything that must be queried, audited, evaluated, or analysed stays
structured; only genuinely free-form context goes in a bounded metadata
field.

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id |
| `recovery_case_id` | owning case |
| `cycle_number` | 1, 2, 3 … ordinal within the case (supports "which decision cycle?") |
| `decision_timestamp` | when the decision was made |
| `payment_amount_at_decision` | the amount used in the EIRV calc (persisted — see "EIRV persistence") |
| `recommended_action` | best economic action **pre-policy** (`RETRY`/`MESSAGE`/`NO_ACTION`) |
| `final_action` | the authorized action actually decided on (`RETRY`/`MESSAGE`/`NO_ACTION`) — stored **separately** from `recommended_action` |
| `decision_reason` | why `recommended_action` won; why `final_action` differs (e.g. policy block) |
| `policy_version_ref` | the policy identity + version evaluated this cycle |
| `decision_engine_version` | optional lightweight label (build/version of the engine) — **not** load-bearing for reconstruction (see "Version traceability") |
| `status` | e.g. `DECIDED` / `EXECUTING` / `RESOLVED` (Phase 1B refines) |
| — links — | `Prediction` (1..N), `PolicyEvaluation` (1..N), `Intervention` (0..1), `Outcome` (0..1) |
| — value context — | per candidate action: `{ action, recovery_probability (via Prediction), cost_used, eirv_value }` |

`model_version` is **not** a direct column: a DecisionRecord's model
reference is derived from its `Prediction`s (all share one `ModelVersion`
in the MVP).

#### `Recommendation`

Not a separate entity — it is `DecisionRecord.recommended_action` (plus
the value context that justifies it). Definition:

> The action the decision engine believes provides the best economic
> outcome **before** policy authorization.

MVP values: `RETRY`, `MESSAGE`, `NO_ACTION`. **`Recommendation ≠ Final
action`** and **`Recommendation ≠ Execution`** — `recommended_action =
RETRY` never implies RETRY was executed.

#### `PolicyEvaluation`

Policy authorization is a **distinct stage** after the recommendation, and
the Policy Engine keeps its **unconditional veto** (ADR-004). One
`PolicyEvaluation` per candidate action the veto loop checked.

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id |
| `decision_record_id` | owning decision cycle |
| `action` | the candidate action evaluated |
| `policy_id` / `policy_version` | which merchant policy + version governed this evaluation |
| `result` | `ALLOWED` \| `BLOCKED` |
| `reason_code` | machine-readable (e.g. `MAX_RETRY_LIMIT`, `MAX_CONTACTS`, `CHANNEL_DISABLED`, `NO_CONSENT`, `AMOUNT_LIMIT`, `RISK_FLAG`) |
| `reason` | human-readable detail |
| `evaluated_at` | timestamp |

The data model must be able to answer, per decision:
*what was recommended, why, which policy + version applied, was it allowed,
if blocked why, what was the final action* — without re-running today's
policy. The ML/economic recommendation and the policy authorization stay
distinct records.

#### `Intervention`

> An action that was actually **attempted / executed**.

- Created **only** when `final_action ∈ {RETRY, MESSAGE}`.
  `final_action = NO_ACTION` ⇒ **no `Intervention`** (never fabricate one
  just because a `DecisionRecord` exists).
- `DecisionRecord 1 ── 0..1 Intervention` for the MVP. (One decision cycle
  → at most one attempted action. Channel fallback for `MESSAGE` is hidden
  inside the message gateway, not modelled as multiple Interventions. If a
  future need for multiple attempts per decision appears, promote to
  `1 ── 1..N` then — not now.)
- Distinguishes **"the system decided to execute RETRY"** from
  **"the provider successfully executed RETRY"** via `execution_status`.

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id |
| `decision_record_id` | owning decision cycle |
| `action` | `RETRY` \| `MESSAGE` |
| `channel` | for `MESSAGE`: concrete channel (`SIMULATED` for MVP) |
| `execution_status` | vocabulary below |
| `requested_at` | when we dispatched the provider call |
| `resolved_at` | when the provider call resolved |
| `cost_incurred` | the (simulated/configured) cost actually applied |
| `provider_ref` | external reference for audit/idempotency |

**Execution status vocabulary (MVP):**

```
REQUESTED  — Intervention created; provider call dispatched, not yet resolved
ACCEPTED   — provider accepted the action (retry triggered / message queued for delivery)
REJECTED   — provider explicitly refused (invalid state / business rule)
FAILED     — transport/technical failure (timeout, 5xx, gateway unavailable)
```

Rationale: this is the smallest set that lets the *next* decision cycle
reason correctly — `REJECTED` (permanent, don't repeat this action) vs
`FAILED` (transient, may retry) is a genuine distinction; `REQUESTED`
covers the async gap. **`SUCCEEDED` is deliberately excluded** — whether a
recovery attempt "succeeded" is a question about the *payment outcome*
(did it recover), which is `Outcome`, not execution status. For the
simulated MVP gateway the terminal status is normally `ACCEPTED`.

#### `Outcome`

> What actually happened to the payment **after** the decision / attempt.

- **Distinct from execution status.** `execution_status = ACCEPTED` with
  `Outcome = NOT_RECOVERED` is normal.
- **Distinct from `RecoveryCase.status`.** Do **not** reuse the case
  state-machine states (`RECOVERED`/`STOPPED`/`EXPIRED`/…) as the Outcome
  vocabulary just because they overlap. `Outcome` describes the observed
  result of one decision cycle; `RecoveryCase.status` is the case's
  overall lifecycle state.
- Attaches to the **`DecisionRecord`** (the cycle whose consequence it is)
  and *optionally* references the `Intervention` it followed. A
  `NO_ACTION` cycle can still have an `Outcome` (natural recovery in the
  observation window).
- **Delayed / non-immediate:** an outcome need not occur right after
  execution. `observed_at` records when recovery / non-recovery was
  actually observed, which may lag `resolved_at`. One `Outcome` per cycle,
  written once its observation window resolves; immutable thereafter.

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id |
| `decision_record_id` | the decision cycle this is the result of |
| `intervention_id` | nullable — the attempt it followed, if any |
| `result` | `RECOVERED` \| `NOT_RECOVERED` (MVP — matches `ml/labels.md`'s binary label) |
| `recovery_amount` | amount recovered (0 if not recovered) |
| `observed_at` | when the result was actually observed (may lag execution) |
| `recorded_at` | system timestamp |

```
Execution result ≠ Payment outcome ≠ RecoveryCase status
```

#### Multiple decision cycles (mandatory)

A single `RecoveryCase` is evaluated repeatedly. Each pass through
`ANALYZING` produces a **new** `DecisionRecord` with its **own**
predictions, model version, EIRV, recommendation, policy evaluations,
final action, and (later) intervention + outcome.

```
RecoveryCase RC-001
├── DecisionRecord D1 (cycle 1) ── Predictions(D1) ── PolicyEval(D1) ── Intervention I1 ── Outcome O1
├── DecisionRecord D2 (cycle 2) ── Predictions(D2) ── PolicyEval(D2) ── Intervention I2 ── Outcome O2
└── …
```

- **D1 is never mutated when D2 occurs.** Every cycle is independently
  auditable — required for audit, debugging, experimentation, model
  evaluation, ML training, understanding how predictions changed, and
  demonstrating the learning loop.
- `cycle_number` orders them; `RecoveryCase 1 ── * DecisionRecord`.

#### Model-version traceability

- **Every `Prediction` references the exact immutable `ModelVersion`**
  (`model_version_id`), not just `model_name`. A historical decision stays
  bound to the precise model that made it, even after promotion to a newer
  version.
- A `DecisionRecord`'s model reference is **derived** from its
  `Prediction`s (one `ModelVersion` per cycle in the MVP).
- The full `ModelVersion` entity is a later Phase 1A step; 1A.2 fixes only
  the **relationship requirement** (`Prediction → ModelVersion`, mandatory,
  exact-version).

#### Version traceability — minimum to reconstruct a historical decision

| Version concept | Where / how | Required for reconstruction? |
|---|---|---|
| **`ModelVersion`** | on each `Prediction` (`model_version_id`) | **Yes** — per prediction, exact version |
| **Policy version** | on the `DecisionRecord` (`policy_version_ref`) and each `PolicyEvaluation` | **Yes** — which policy config authorized/blocked |
| **`feature_schema_version`** | *derived* — pinned by `ModelVersion` (each version is trained against a known feature schema), and the actual inputs are in the persisted `feature_snapshot` | **No separate storage** on `DecisionRecord` |
| **`decision_engine_version`** | optional lightweight label on `DecisionRecord` | **No** — the EIRV *formula* is fixed (ADR-003) and the EIRV *inputs and outputs* are persisted (below), so no engine re-run is needed to reconstruct |

So the minimum is: `Prediction → exact ModelVersion` + `DecisionRecord →
policy version` + persisted EIRV inputs/outputs. `feature_schema_version`
is derivable; `decision_engine_version` is debugging metadata only.

#### EIRV persistence — "why did RETRY win?" must be answerable

The EIRV formula is unchanged (ADR-003:
`[P(recover|a) − P(recover|none)] × amount − cost(a)`). What must be
**persisted** so a past financial decision is explainable without today's
model/policy/config:

| Value | How it is preserved |
|---|---|
| per-action `recovery_probability` (incl. the `NO_ACTION` baseline) | **related record** — the `Prediction` rows |
| `payment_amount` used | **persisted directly** — `DecisionRecord.payment_amount_at_decision` |
| per-action `cost_used` (simulated/configured at decision time) | **persisted directly** — value context (config can change, so it must be captured) |
| per-action `eirv_value` | **persisted directly** — value context (also independently **re-derivable** from the three above via the fixed formula, so it is both stored and checkable) |
| `recommended_action` + why it won | **persisted directly** — `recommended_action` + `decision_reason` |

This lets an auditor read one `DecisionRecord` (+ its `Prediction`s) and
answer *"why did RETRY win?"* directly, and independently recompute EIRV
to verify — with **no** dependency on the current model, policy, or cost
config. Nothing is duplicated beyond these few numbers; feature detail
lives once in the `Prediction` feature snapshot.

#### Economic values must stay auditable

Because decisions involve money, the relevant economic context at decision
time (probabilities via `Prediction`, amount, costs, EIRV values,
recommendation, policy version) is preserved as above. Recalculating with
*today's* model / policy / cost / config is **not** an acceptable
substitute for a historical explanation. This is the smallest structure
that gives reproducibility without duplicating everything everywhere.

#### Privacy

`Prediction`, `DecisionRecord`, `PolicyEvaluation`, `Intervention`, and
`Outcome` use **internal references** (`merchant_id`, `payment_id`,
`recovery_case_id`, `decision_record_id`, `model_version_id`,
`intervention_id`) — they do **not** duplicate customer/payment-sensitive
data. The `Prediction` feature snapshot already follows the data
classification in `architecture/privacy-architecture.md` (aggregates /
task-scoped derived fields, never raw PII or card data). The LLM stays
**outside** the authoritative decision/execution path (ADR-004) — it may
later phrase an explanation from these records but never produces or
authorizes them.

#### Training-loop & experiment compatibility (not designed here)

- `TrainingExample` is **not** designed in this phase. But the records
  above already provide the raw material a future training observation
  needs: **context** (`Prediction.feature_snapshot`), **treatment/action**
  (`Prediction.action` / `Intervention.action`), **decision context**
  (`DecisionRecord`), **outcome / recovery result** (`Outcome`). One
  `RecoveryCase` → **multiple** training observations (per cycle, per
  action). The rejected "one RecoveryCase = one TrainingExample" (Phase
  0.5) is not reintroduced.
- `Experiment` / `ExperimentAssignment` are finalized in Phase 1A.3 (see
  below and ADR-011): the assignment lives on the `RecoveryCase`
  (immutable, one per case), and every `DecisionRecord` under that case is
  attributable to its experimental/control context **through the case** —
  not via its own field.

#### Conceptual relationship model

```
RecoveryCase
     │ 1:N
     ▼
DecisionRecord                          (one per evaluate→decide cycle; never overwritten)
     │
     ├── 1:N ── Prediction ── *:1 ── ModelVersion   (one Prediction per candidate action)
     │
     ├── 1:N ── PolicyEvaluation                    (one per candidate policy-checked)
     │
     ├── 0..1 ── Intervention ── execution_status   (only for final_action RETRY / MESSAGE)
     │                 │
     │                 └── 0..1 ──┐
     │                            ▼
     └── 0..1 ────────────────► Outcome              (result of this cycle; delayed OK)
```

(`RecoveryCase`'s own `ExperimentAssignment` and `Policy` relationships are
shown in the full "Conceptual relationship model" under Phase 1A.3 below —
omitted here to keep this diagram scoped to the decision lifecycle.)

#### End-to-end example — two decision cycles on one case

```
Payment P-1001  amount ₹1,000  status FAILED
RecoveryCase RC-001  (opened via eligibility gate)

── DecisionRecord D1  (cycle_number 1; all Predictions below from model_version v1.3.2) ──
Predictions:
   Prediction(action=RETRY,     recovery_probability=0.72, model_version=v1.3.2)
   Prediction(action=MESSAGE,   recovery_probability=0.61, model_version=v1.3.2)
   Prediction(action=NO_ACTION, recovery_probability=0.43, model_version=v1.3.2)   ← baseline
Value context (amount ₹1,000; illustrative simulated costs):
   cost_used(RETRY)=₹2   eirv_value(RETRY)   = (0.72-0.43)×1000 - 2  ≈ ₹288   ← highest
   cost_used(MESSAGE)=₹3 eirv_value(MESSAGE) = (0.61-0.43)×1000 - 3  ≈ ₹177
   eirv_value(NO_ACTION) = 0 (reference)
recommended_action = RETRY
PolicyEvaluation(action=RETRY, policy_version=v3, result=ALLOWED, evaluated_at=…)
final_action = RETRY
Intervention I1(action=RETRY, execution_status=ACCEPTED, requested_at=10:00, resolved_at=10:00)
Outcome O1(decision_record=D1, intervention=I1, result=NOT_RECOVERED, observed_at=10:20)

── DecisionRecord D2  (cycle_number 2; all Predictions below from model_version v1.4.0) ──
Predictions:
   Prediction(action=RETRY,     recovery_probability=0.35, model_version=v1.4.0)
   Prediction(action=MESSAGE,   recovery_probability=0.66, model_version=v1.4.0)
   Prediction(action=NO_ACTION, recovery_probability=0.40, model_version=v1.4.0)
Value context:
   eirv_value(MESSAGE) highest → recommended_action = MESSAGE
PolicyEvaluation(action=MESSAGE, policy_version=v3, result=ALLOWED)
final_action = MESSAGE
Intervention I2(action=MESSAGE, channel=SIMULATED, execution_status=ACCEPTED)
Outcome O2(decision_record=D2, intervention=I2, result=RECOVERED, recovery_amount=₹1,000,
           observed_at=10:52)
```

**D1 is unchanged by D2.** Both cycles remain independently auditable,
each with its own predictions, model version, EIRV context, recommendation,
policy evaluation, final action, intervention, and outcome.

A blocked variant (recommendation ≠ final action):

```
── DecisionRecord D3 ──
recommended_action = RETRY
PolicyEvaluation(action=RETRY, policy_version=v3, result=BLOCKED, reason_code=MAX_RETRY_LIMIT)
PolicyEvaluation(action=MESSAGE, policy_version=v3, result=BLOCKED, reason_code=MAX_CONTACTS)
final_action = NO_ACTION
Intervention: NULL
Outcome O3(decision_record=D3, intervention=NULL, result=NOT_RECOVERED, observed_at=…)
```

#### Decision audit questions (every DecisionRecord must eventually answer)

```
1. Which RecoveryCase?                         recovery_case_id
2. Which payment?                              → via RecoveryCase.payment_id
3. Which decision cycle?                        cycle_number
4. When was the decision made?                  decision_timestamp
5. What candidate actions were evaluated?        the Prediction set (RETRY/MESSAGE/NO_ACTION)
6. What did the model predict for each?          Prediction.recovery_probability per action
7. Which ModelVersion produced those?            Prediction.model_version_id (exact)
8. What economic values were calculated?         value context: cost_used + eirv_value per action, amount
9. Which action was recommended?                 recommended_action
10. Which policy / version was evaluated?         PolicyEvaluation.policy_id + policy_version
11. Was the recommendation allowed?              PolicyEvaluation.result for that action
12. If blocked, why?                             PolicyEvaluation.reason_code + reason
13. What was the final action?                   final_action
14. Was an intervention executed?                Intervention present? (NULL for NO_ACTION)
15. What was the execution result?               Intervention.execution_status
16. What outcome occurred?                       Outcome.result (+ recovery_amount)
17. When was that outcome observed?              Outcome.observed_at
18. What happened to the RecoveryCase afterward? RecoveryCase.status + status history / next DecisionRecord
```

#### Data contract vs. implementation

1A.2 fixes entities, relationships, business rules, field semantics, and
vocabularies for the decision lifecycle. Phase 1B fixes SQLAlchemy models,
PostgreSQL types, keys, indexes, constraints, migrations, repositories,
and Pydantic schemas. The full `ModelVersion`, `Experiment`,
`ExperimentAssignment`, and `TrainingExample` contracts are later phases.

### Model, Policy & Experiment data contract — Phase 1A.3

Conceptual + logical contract for `ModelVersion`, `Policy`, `Experiment`,
and `ExperimentAssignment`. Does **not** fix SQL types, keys, indexes, or
migrations (Phase 1B), and does not change anything approved in Phase
0/0.5/0.6/1A.1/1A.2 (see ADR-011).

#### `ModelVersion`

> One exact, reproducible ML model version.

**Immutable once created** — the artifact and its substantive metadata
never change:

```
model artifact identity        training dataset snapshot identity
artifact checksum/hash         feature schema identity
algorithm/model type           training configuration / hyperparameters
training code/pipeline version random seed (where relevant)
evaluation summary             creation timestamp
```

If any of the above materially changes, that is a **new** `ModelVersion`
— never an edit to an existing one. Only the **lifecycle `status`** is
mutable (see below); the model itself never changes underneath a status
change.

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id (immutable identity) |
| `model_role` | which job this model serves (e.g. `recovery_prediction`) — see promotion rule |
| `algorithm` | model type (e.g. logistic regression, LightGBM) |
| `artifact_ref` | reference to the stored artifact |
| `artifact_checksum` | hash of the artifact, for integrity/reproducibility |
| `training_dataset_snapshot_id` | identity of the exact training data snapshot used (see below) |
| `feature_schema_id` | identity of the feature schema this model expects (carries `feature_schema_version`) |
| `training_config` | hyperparameters / training configuration |
| `training_pipeline_version` | code/pipeline identity that produced it |
| `random_seed` | where relevant, for reproducibility |
| `evaluation_summary` | calibration/AUC/uplift/decision-quality results at training time (see `ml/evaluation.md`) |
| `status` | lifecycle status (below) — the only mutable field |
| `created_at` | immutable |
| `status_changed_at` | when `status` last changed |

**Training dataset identity.** `ModelVersion → Training Dataset Snapshot`
is a required, immutable, reproducible reference (a content/hash identity
is sufficient conceptually). No separate dataset registry is introduced —
this is only the identity requirement; physical storage is Phase 1B / the
ML implementation.

**Feature schema.** `Prediction → ModelVersion → Feature Schema Version` —
the `ModelVersion` carries the feature-schema identity a `Prediction` was
scored against. This is consistent with, and does **not** reopen, the
Phase 1A.2 rule `Prediction → exact ModelVersion` with a *derived* (not
independently stored) `DecisionRecord` model reference — see "ModelVersion
— prediction/decision traceability" above.

##### Lifecycle

```
DRAFT → VALIDATED → PROMOTED → RETIRED
  │         │
  └───► REJECTED ◄───┘
```

| Status | Meaning |
|---|---|
| `DRAFT` | Trained, not yet evaluated against promotion criteria. |
| `VALIDATED` | Passed the required validation/evaluation criteria; eligible for promotion (or use as an experimental candidate). |
| `PROMOTED` | The current default production model **for its `model_role`**. |
| `RETIRED` | Was previously `PROMOTED` (or usable), no longer the production default. |
| `REJECTED` | This exact immutable version failed evaluation / was explicitly rejected; cannot be promoted **in that form**. |

**Allowed transitions:** `DRAFT → VALIDATED`, `DRAFT → REJECTED`,
`VALIDATED → PROMOTED`, `VALIDATED → REJECTED`, `PROMOTED → RETIRED`.
**Forbidden:** `REJECTED → PROMOTED` (or `REJECTED →` anything but staying
`REJECTED`), `RETIRED → PROMOTED` (re-promotion means training a new
version, not reviving an old one — keeps history unambiguous), skipping
`VALIDATED` to reach `PROMOTED` directly. A rejected/retired model version
is never edited into a promotable one — retraining produces a **new**
`ModelVersion` (`model-v8 REJECTED` → new training → `model-v9
VALIDATED → PROMOTED`).

##### Promotion rule

> For a given `model_role`, there is **exactly one** default production
> (`PROMOTED`) `ModelVersion` at a time.

This does **not** mean only one `ModelVersion` may exist — `VALIDATED`
candidates (including ones used only for controlled experiments) and
`RETIRED` history coexist freely. "One promoted" constrains the
*production default*, not the population of model versions.

```
recovery_prediction_model role:
   model-v7  PROMOTED     ← production default
   model-v8  VALIDATED    ← experimental candidate, not default
   model-v6  RETIRED      ← history
```

#### `Policy`

> One immutable, versioned set of merchant-configured recovery rules.

- **Belongs to a `Merchant`.** `Merchant 1 ── * Policy version`.
- **Immutable per version.** A policy change creates a **new** `Policy`
  version; historical versions are never edited in place. Exactly one
  version per merchant is "current active"; the rest are retained for
  historical traceability. (The Relationships block reads
  `Merchant 1──* Policy version` — 1A.3 makes the versioning explicit and
  mandatory.)
- The system must be able to identify which exact policy version was
  evaluated for any historical decision — already required by
  `PolicyEvaluation.policy_version` (Phase 1A.2) and unchanged here.

**Policy data vs. Policy Engine (explicit distinction):**

| | Policy (data) | Policy Engine (logic) |
|---|---|---|
| Answers | **WHAT** is allowed | **HOW** rules are evaluated |
| Is | configuration, versioned, immutable per version | deterministic code (`decision-engine/policy-engine.md`) |
| Contains | rule *values* | rule *evaluation* |

Conceptual rule categories a `Policy` version may express (not a final
physical schema):

```
max_retry_count            allowed_actions
max_contact_count          allowed_channels
recovery_window            restricted_hours
minimum_amount             consent requirements
maximum_amount             risk thresholds
merchant-specific limits
```

**No arbitrary executable policy code** in the MVP design — policy is
structured data interpreted by the fixed Policy Engine, never code
supplied per merchant. This reaffirms `decision-engine/policy-engine.md`'s
design and ADR-004 (deterministic, non-bypassable veto).

#### `Experiment` / `ExperimentAssignment`

```
Experiment
     ↓
ExperimentAssignment          (one per RecoveryCase, immutable)
     ↓
RecoveryCase
     ↓
DecisionRecord(s)              (every cycle inherits the same arm)
```

**Assignment level: `RecoveryCase`, not `DecisionRecord`.** A case can
have multiple `DecisionRecord`s (re-evaluation cycles); assigning per
cycle would let a case contaminate its own comparison by switching arms
mid-case. One `RecoveryCase` ⇒ one `ExperimentAssignment` ⇒ every
`DecisionRecord` under it inherits the same arm.

```
RecoveryCase RC-100
   ExperimentAssignment = TREATMENT
        ├── DecisionRecord #1
        ├── DecisionRecord #2
        └── DecisionRecord #3      (all TREATMENT — never switches)
```

**`CONTROL` / `TREATMENT` semantics (flexible, not action-forcing):**

| Arm | Meaning |
|---|---|
| `CONTROL` | The existing/default strategy, used as the comparison baseline |
| `TREATMENT` | The experimental strategy being evaluated against control |

"Treatment" does **not** mean "force a specific action." It may instead
mean a different `ModelVersion`, a different decision strategy, a
different policy-compatible configuration, or a different economic
(EIRV) configuration — whatever the experiment is testing. Both arms
retain the full MVP candidate set:

```
CONTROL:            RETRY → prediction/value | MESSAGE → prediction/value | NO_ACTION → baseline
TREATMENT:           RETRY → prediction/value | MESSAGE → prediction/value | NO_ACTION → baseline
```

An experiment must **never** force an intervention just because a case is
in `TREATMENT` — `NO_ACTION` stays a first-class candidate in every arm.

**Immutability.** Once a `RecoveryCase` is assigned `CONTROL` or
`TREATMENT`, that assignment does not change for the life of the case.
`CONTROL → TREATMENT` mid-case is not supported. Historical assignment
stays auditable.

**Supersedes `recovery_case.experiment_arm`.** The Phase 0.6-era
placeholder string field is **superseded** by `ExperimentAssignment` as
the authoritative assignment record — not a second source of truth. (The
physical decision — drop the column, keep it as a denormalized mirror, or
migrate it — is Phase 1B; the *logical* authority is `ExperimentAssignment`
from this phase onward.)

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id |
| `experiment_id` | owning `Experiment` |
| `recovery_case_id` | the assigned case (one assignment per case) |
| `arm` | `CONTROL` \| `TREATMENT` |
| `experimental_config_ref` | nullable — what the arm actually varies (e.g. a `ModelVersion` reference for a model experiment); reference, not a duplicate copy |
| `assigned_at` | immutable |

**Experimental model versions.** An experiment's `TREATMENT` arm may
reference a `VALIDATED` (not yet `PROMOTED`) `ModelVersion` for its
predictions, without touching the production default:

```
Production default (all CONTROL, and TREATMENT unless overridden): model-v7 PROMOTED
Experiment "uplift-v2-trial" TREATMENT arm: model-v8 VALIDATED (experimental)
```

The `ExperimentAssignment` (or its referenced config) makes this
auditable by **reference** — it does not duplicate `ModelVersion` data.

**Policy and eligibility are never bypassed.** The ordering is unchanged
and non-negotiable:

```
Experiment / experimental strategy
        ↓
Predictions
        ↓
EIRV
        ↓
Recommendation
        ↓
Policy Evaluation          ← ADR-004 unconditional veto, still applies
        ↓
Final Action
```

**Not** `Experiment → Final Action`. An experiment can change *which
model or strategy produces the recommendation*; it cannot authorize an
action policy would block, and it cannot skip recovery eligibility.

**Evaluation model for the hackathon (offline-first).** The primary,
safe evaluation mechanism is **offline**:

```
Historical / Synthetic Data → Control Strategy → Treatment Strategy
    → Compare outcomes / incremental value
```

Controlled *production* experimentation (live `CONTROL`/`TREATMENT`
cohorts) may be documented as a future capability, but only ever
constrained by eligibility, policy, risk limits, small controlled
cohorts, and auditability — never unrestricted live exploration, and
never reinforcement-learning-style unconstrained exploration (this
reaffirms the RL rejection in `ml/uplift-modelling.md`).

#### Immutability / audit principles (explicit, Phase 1A.3)

```
ModelVersion            — immutable (status field is the sole exception)
Policy version           — immutable
Prediction                — immutable
DecisionRecord             — immutable
ExperimentAssignment        — immutable
PolicyEvaluation              — immutable (historical)
Intervention                   — immutable (historical) once resolved
Outcome                          — immutable once resolved
```

No discrepancy with prior phases was found — this restates and makes
explicit what Phase 1A.2 already implied ("historical `DecisionRecord`s
are immutable and never overwritten").

#### Conceptual relationship model

```
Merchant
   │
   ├──────────────── Policy
   │                    └── Policy version(s), immutable
   │
   └──────────────── RecoveryCase
                         │
                         ├── ExperimentAssignment (0..1, immutable)
                         │        └── Experiment
                         │
                         └── DecisionRecord (1..*, immutable, per cycle)
                                 │
                                 ├── Prediction (1 per candidate action)
                                 │       └── ModelVersion (exact, immutable)
                                 │
                                 ├── PolicyEvaluation (1 per candidate checked)
                                 │       └── (policy_id, policy_version)
                                 │
                                 ├── Intervention (0..1; only RETRY/MESSAGE)
                                 │
                                 └── Outcome (0..1)
```

All Phase 1A.2 cardinalities are preserved unchanged.

#### Data contract vs. implementation (1A.3)

Fixed here: entity purpose, immutability rules, lifecycle vocabulary, the
one-promoted-per-role rule, reproducibility field list, the
assignment-level/immutability/semantics of `ExperimentAssignment`, and the
experiment/policy/eligibility ordering. **Not** fixed (Phase 1B or later):
exact SQL types, indexes, FK mechanics, migration structure, artifact
storage implementation, exact dataset hash/storage mechanism, exact
policy JSON/table representation, exact experiment-assignment constraints.

### Training data contract — Phase 1A.4

How completed decision cycles become valid ML training observations. This
is the last data-contract phase before implementation — deliberately
narrow. No feature store, MLflow, Kafka/Spark, data warehouse, distributed
training, RL, or production ML platform is introduced.

#### `TrainingExample` — the ML observation unit

> One `TrainingExample` = **one `DecisionRecord` × one candidate action**.

```
DecisionRecord D1
   ├── TrainingExample(action = RETRY)
   ├── TrainingExample(action = MESSAGE)
   └── TrainingExample(action = NO_ACTION)
```

Rationale: the model predicts per action; `action` is the S-learner's
treatment feature; `RETRY`/`MESSAGE`/`NO_ACTION` are already independent
per-action `Prediction`s; it avoids a three-action blob; it composes
naturally with repeated `DecisionRecord`s; and it keeps *prediction*
separate from *observed outcome*.

`TrainingExample` is a derived ML artifact, not a business record — it is
generated from already-persisted immutable records (`DecisionRecord`,
`Prediction`, `Outcome`, `RecoveryCase`, `ExperimentAssignment`) and never
edited in place.

| Field (conceptual) | Semantics |
|---|---|
| `id` | internal id |
| `decision_record_id` | the cycle this row derives from |
| `recovery_case_id` | **grouping key for leakage-safe splitting** (see below) |
| `action` | the candidate action this row is about (`RETRY`/`MESSAGE`/`NO_ACTION`) — the treatment feature |
| `observed_action` | what actually happened this cycle (see "Observed-action rule") — used to decide whether this row carries an *observed* label |
| `is_observed` | `true` iff `action == observed_action` **and** the outcome for that action is usable — only observed rows carry an outcome label |
| `feature_snapshot` | features **as of the `DecisionRecord`** (from the matching `Prediction`; see "Leakage") — never post-decision data |
| `outcome_label` | `RECOVERED` / `NOT_RECOVERED` **only when `is_observed`**; otherwise null (this row is context-only / for the treatment representation, not a counterfactual label) |
| `recovery_amount` | from the `Outcome`, when `is_observed` and `RECOVERED` |
| `observation_timestamp` | `Outcome.observed_at` (may lag the decision — delayed outcomes) |
| `model_version_id` | the exact `ModelVersion` that produced this cycle's `Prediction`s (from `Prediction`, not a new column of truth) |
| `experiment_arm` | `CONTROL` / `TREATMENT` **inherited from the `RecoveryCase`'s `ExperimentAssignment`** (case-level — never a `DecisionRecord`-level relation) |
| `created_at` | when the row was generated |

Fields deliberately **not** included as separate stores of truth: policy
result (read via `PolicyEvaluation`), `payment_amount` (via
`DecisionRecord`), per-action EIRV (via `DecisionRecord` value context).
The exact physical shape (one row per action vs. a compact
per-`DecisionRecord` layout with an action column) is Phase 1B; the
**logical unit is `DecisionRecord × action`**.

#### Prediction is not an observed outcome (critical)

A `Prediction` for an action is **not** an observed outcome for that
action. In a cycle where `final_action = RETRY`:

- we observed the outcome **under `RETRY` only**;
- we did **not** observe what `MESSAGE` or `NO_ACTION` would have produced.

So `TrainingExample(action=MESSAGE)` in that cycle carries **no**
`outcome_label` — its `MESSAGE` value stays a *prediction*, never a
manufactured counterfactual label. **Never** write three
`outcome_label = RECOVERED` rows because one `RETRY` recovered.

#### Observed-action rule

`observed_action` is derived from **what actually happened**, not the
recommendation:

```
recommended_action = RETRY,  policy = BLOCKED,  final_action = NO_ACTION
        ⇒ observed_action = NO_ACTION   (there was no RETRY intervention)
```

- `final_action = RETRY` or `MESSAGE`, `Intervention.execution_status ∈
  {ACCEPTED}` → `observed_action = final_action`.
- `final_action = NO_ACTION` → `observed_action = NO_ACTION` (no
  `Intervention` required — see below).
- `final_action = RETRY`/`MESSAGE` but `Intervention.execution_status ∈
  {REJECTED, FAILED}` → **not a clean observed treatment**: `observed_action`
  is recorded but the row is marked `is_observed = false` for the intended
  treatment (decision-to-execute ≠ execution-succeeded ≠ recovery-outcome —
  see "Failed execution"). No elaborate causal-censoring methodology for
  the MVP.

#### `NO_ACTION` training examples

`NO_ACTION` is a legitimate action/treatment. A cycle with
`final_action = NO_ACTION` (no `Intervention`) that later resolves an
`Outcome` (`RECOVERED` = natural recovery, or `NOT_RECOVERED`) produces a
**valid** observed `TrainingExample(action=NO_ACTION, is_observed=true)`.
An `Intervention` row is **not** required. (`NO_ACTION ≠ STOPPED`, and
`NO_ACTION → no Intervention` — both unchanged.)

#### What counts as a valid (observed) training example

Generally requires: a valid `DecisionRecord` + a known action context + a
**resolved, usable `Outcome`** for the observed action. Excluded:
incomplete/unresolved cases, `FAILED` `RecoveryCase`s and other corrupted
records, failed system execution where no meaningful action/outcome was
observed, policy-only/simulated evaluations with no outcome, and duplicate
observations. Terminal-case handling stays as `ml/labels.md` defines it
(`RECOVERED` / `STOPPED` / `EXPIRED` contribute; `FAILED` excluded) — this
phase does not loosen it.

#### Repeated decision cycles

Each `DecisionRecord` on a `RecoveryCase` can generate its own
`TrainingExample`s per the rules above. Cycles are **not** collapsed into
one row.

```
RC-1 ── D1 ── TrainingExample(s)
       └ D2 ── TrainingExample(s)
       └ D3 ── TrainingExample(s)
```

But observations from the same case are **correlated**, not independent:

> `TrainingExample` is the observation unit; `RecoveryCase` remains the
> **grouping unit** for leakage-safe evaluation, splitting, and
> experimentation.

#### Data leakage prevention

`feature_snapshot` must contain only information available **at the time
of the `DecisionRecord`**. The `Outcome` is the **label**, never a
feature. The following must never leak into features: the later `Outcome`,
`recovery_amount`, `observed_at`; any future `DecisionRecord`, future
`PaymentEvent`, or later `Intervention` result. (The feature snapshot is
already immutable and captured at prediction time — Phase 1A.2 — this
restates why.)

#### Case-level train / validation / test splitting

Splitting happens at **`RecoveryCase` level**, not `TrainingExample`
level:

```
RC-001 ── TE-001, TE-002, TE-003   → all in ONE split
```

All `TrainingExample`s from a case stay in the same split. This prevents
same-case observations appearing across splits and inflating apparent
performance.

#### Delayed outcomes

A `TrainingExample` is not final until the cycle's observation window has
resolved (`Outcome.observed_at` set, or the window closed as
`NOT_RECOVERED`). Immediate outcomes are not required.

#### Experiment arm

`experiment_arm` on a `TrainingExample` is **inherited from the
`RecoveryCase`'s `ExperimentAssignment`** (case-level, Phase 1A.3). There
is no `DecisionRecord → ExperimentAssignment` relation.

#### Simulator ground truth ≠ observational outcomes

Keep distinct (see also "Simulator ground truth vs. experiment
CONTROL/TREATMENT" and `data/synthetic-data.md`):

| | Provides |
|---|---|
| **Simulator hidden ground truth** | the *true potential outcomes under every action* — usable (evaluation only) to check whether treatment-effect / uplift estimates are correct |
| **Observational training data** (`TrainingExample`) | only the outcome of the **action actually observed** each cycle — **no** counterfactuals |

The observational dataset does **not** supply all counterfactual
outcomes. No sophisticated statistical framework is added here; that is an
ML/evaluation-phase concern if needed.

#### S-learner compatibility

`TrainingExample` gives a clean `(context/features, action/treatment,
observed outcome)` triple, which is exactly what the S-learner-style
shared model consumes. This does **not** lock the system to S-learner —
the Phase 4 comparison of S-learner / T-learner / other methods
(`ml/uplift-modelling.md`) is unchanged; those methods consume the same
per-`(cycle, action)` rows.

#### Training dataset snapshot

When a model is trained, the exact set of `TrainingExample`s used is a
**training dataset snapshot** with a reproducible identity (a
deterministic content hash / equivalent conceptual identity is enough).
`ModelVersion.training_dataset_snapshot_id` (Phase 1A.3) references it. No
dataset registry is built; physical storage is Phase 1B / ML
implementation.

#### Training pipeline (intended flow)

```
DecisionRecord ── Outcome resolved? ──► TrainingExample generation
   ──► Training Dataset ──► case-level split ──► train model ──► evaluate
   ──► create ModelVersion (DRAFT) ──► VALIDATED ──► promotion decision
```

A model that fails evaluation becomes `REJECTED` and can never later
become `PROMOTED` (Phase 1A.3 / ADR-011); retraining produces a new
`ModelVersion`.

#### Data contract vs. implementation (1A.4)

Fixed here: `TrainingExample` as the observation unit; the
`DecisionRecord × action` granularity; the observed-action rule;
prediction ≠ observed outcome (no manufactured counterfactuals);
`NO_ACTION` observations without an `Intervention`; repeated-cycle rows;
`RecoveryCase` as the grouping/splitting unit; `Outcome` as the label;
delayed-outcome eligibility; case-level splitting; leakage rule;
snapshot-identity requirement. **Not** fixed (Phase 1B / ML
implementation): physical `training_example` shape and types, the exact
snapshot hash mechanism, the exact split ratios/seed, and any
statistical/uplift estimator details.

## 4. Alternatives considered

Considered modelling `Intervention` and `Outcome` as a single combined
table. Rejected — an intervention can be executed without an outcome being
known yet (e.g. a message was sent but the customer hasn't acted), so they
have different, asynchronously-arriving lifecycles and must be separate
rows/tables.

Considered making `RecoveryCase` strictly 1:1 with `Payment` permanently
(`UNIQUE(payment_id)`). Kept as an explicit MVP simplification —
**at most one active case per eligible failed payment** (see the "Core
data contract" and section-3 notes) — but not a permanent unique
constraint, and flagged as something that will need revisiting when
checkout-abandonment/subscription event types are added post-MVP (see
`product/mvp-scope.md`), since those events don't always map to a single
`Payment` row. Multi-attempt history is already captured by `PaymentEvent`
regardless.

Considered folding the recommendation and policy result into the
`Intervention` row instead of a separate `DecisionRecord`. Rejected — an
`Intervention` only exists once something is executed, so a
recommendation that was *blocked* (recommended `RETRY`, executed
`NO_ACTION`) would have no home, and the "why did the system decide this"
audit question could not be answered for cases where nothing happened.
`DecisionRecord` exists for every evaluate→decide cycle, executed or not.

Considered a single `Prediction` per `DecisionRecord` carrying all action
probabilities in one row/blob. Rejected for the contract — the learning
loop and evaluation both operate at (context, **action**, outcome)
granularity, so a **per-action** `Prediction` (one per candidate action
per cycle) keeps that queryable and keeps each probability bound to its
exact `ModelVersion`. Phase 1B may still store them compactly, but the
logical unit is per-action.

Considered attaching `Outcome` to `Intervention` only (as the earlier
sketch did). Rejected — a `NO_ACTION` decision cycle has no `Intervention`
but can still have an observed `Outcome` (natural recovery in the window).
So `Outcome` attaches to the `DecisionRecord` (the cycle it results from)
and *optionally* references the `Intervention`. Cardinality is unchanged
(0..1); only the primary parent moved.

Considered reusing `RecoveryCase.status` values as the `Outcome`
vocabulary. Rejected — they answer different questions (cycle result vs
case lifecycle state) and conflating them makes both un-analysable.
`Outcome.result` is its own minimal vocabulary (`RECOVERED` /
`NOT_RECOVERED`).

Considered assigning `ExperimentAssignment` per `DecisionRecord` instead
of per `RecoveryCase`. Rejected — a case can have multiple decision
cycles, so per-cycle assignment would let a single case contaminate its
own comparison by switching `CONTROL`/`TREATMENT` mid-case. Assignment is
fixed once, at the `RecoveryCase`, and every cycle under it inherits it.

Considered letting `ModelVersion.status` transitions imply the underlying
model can change (e.g. "fix and re-promote" a `REJECTED` version in
place). Rejected — that would break reproducibility for any `Prediction`
already referencing that version. Only `status` is mutable; a materially
different model is always a **new** `ModelVersion`.

Considered `one RecoveryCase = one TrainingExample`. Rejected (already in
Phase 0.5) and finalized against in Phase 1A.4 — a case can run multiple
decision cycles, and the model predicts *per action*, so the observation
unit is `DecisionRecord × candidate action`. `RecoveryCase` stays the
*grouping* unit for leakage-safe splitting.

Considered writing an outcome label for every candidate action in a cycle
(using the observed outcome for all three). Rejected — that manufactures
counterfactual data. Only the `observed_action` gets an `outcome_label`;
the other candidates remain `Prediction`s, not labels.

Considered a per-`DecisionRecord` `TrainingExample` (one row, three action
columns/probabilities). Rejected as the *logical* unit — it reintroduces
the three-action blob and blurs prediction vs. observed outcome. Phase 1B
may still store rows compactly, but the contract is per `(cycle, action)`.

## 5. Why this option

Modelling `RecoveryCase` as the central object (rather than `Payment`)
correctly reflects that the *product* is about the recovery decision
process, not the payment itself — this also makes the audit trail and
learning loop natural: every closed `RecoveryCase` contributes observed
outcome data to the learning system, which is then turned into one or more
`TrainingExample` rows (see "RecoveryCase vs. TrainingExample" above). A
case is a business/audit object; a training example is a derived ML dataset
row — they are not one and the same.

## 6. Example

Full worked example: see `architecture/decision-flow.md` section 6, which
traces `RC-10281` through this exact lifecycle.

## 7. Implementation implications

- `RecoveryCase.status` should be an enum, and every transition should be
  logged (append-only) rather than only keeping the current value, to keep
  the audit trail complete — see `data/database-schema.md`.
- `Prediction` is per `DecisionRecord` **and per candidate action** (not a
  single row per cycle), so each probability stays bound to its exact
  `ModelVersion` and the learning loop can read (context, action, outcome)
  directly — see "Decision data contract".
- Historical `DecisionRecord`s are **immutable** once a cycle resolves and
  are **never overwritten** by a later cycle; re-evaluation always creates
  a new `DecisionRecord` with a higher `cycle_number`.
- Entities, relationships, identifiers, the case lifecycle, and the
  training-data representation are finalized in **Phase 1A (Data Contract
  Finalization)** before Phase 1B turns this into SQLAlchemy models — see
  `docs/README.md` and ADR-007.
- **Phase 1A.2 is complete for the decision lifecycle** (`Prediction`,
  `DecisionRecord`, `PolicyEvaluation`, `Intervention`, `Outcome`) — see
  "Decision data contract" above and ADR-010: per-action predictions with
  exact `ModelVersion`; EIRV inputs/outputs persisted for reconstruction;
  `PolicyEvaluation` distinct from recommendation; `NO_ACTION` never
  creates an `Intervention`; `execution_status` distinct from `Outcome`
  distinct from `RecoveryCase.status`; multiple immutable `DecisionRecord`s
  per case. Phase 1B implements types/keys/indexes/constraints.
- **Phase 1A.1 is complete for `Merchant`, `Payment`, `PaymentEvent`, and
  `RecoveryCase`** (see "Core data contract" above and ADR-009): identity
  is UUID `id` + human-readable `display_id`; `Payment.status` uses the
  lean internal vocabulary with a documented provider mapping;
  `PaymentEvent` is immutable/append-only with the 5-value MVP vocabulary
  and nullable `attempt_number`; a failed payment reaches recovery only
  via the eligibility gate; at most one active `RecoveryCase` per payment.
  Phase 1B implements types/keys/indexes/constraints.
- **Phase 1A.3 is complete for `ModelVersion`, `Policy`, `Experiment`, and
  `ExperimentAssignment`** (see "Model, Policy & Experiment data contract"
  above and ADR-011): `ModelVersion` is immutable except its lifecycle
  `status` (`DRAFT`/`VALIDATED`/`PROMOTED`/`RETIRED`/`REJECTED`), with
  exactly one `PROMOTED` version per `model_role`; `ModelVersion` carries
  reproducibility metadata (artifact, checksum, training dataset snapshot
  identity, feature schema identity, config, pipeline version, seed,
  evaluation summary); `Policy` versions are immutable per merchant;
  `ExperimentAssignment` is assigned at the **`RecoveryCase` level only**
  (immutable, never per `DecisionRecord`), supersedes
  `recovery_case.experiment_arm`, never removes `NO_ACTION`, and never lets
  an experiment bypass eligibility or policy. Phase 1B implements
  types/keys/indexes/constraints.
- **Phase 1A.4 is complete for `TrainingExample`** (see "Training data
  contract" above and ADR-012): one `TrainingExample` = one
  `DecisionRecord` × one candidate action (derived, immutable); a
  `Prediction` is not an observed outcome — only the `observed_action`
  carries an `outcome_label` (no manufactured counterfactuals);
  `NO_ACTION` cycles produce valid observations without an `Intervention`;
  repeated `DecisionRecord`s each contribute rows; `RecoveryCase` is the
  grouping unit for **case-level** train/val/test splitting; `Outcome` is
  the label; features are frozen as of the `DecisionRecord` (no leakage);
  the training set used to train a model is a reproducible **dataset
  snapshot** referenced by `ModelVersion`. **Phase 1A is now complete.**

## 8. Open questions

**Resolved in Phase 1A.1** (ADR-009): identity strategy (UUID `id` +
`display_id`); lean internal `Payment.status` vocabulary + provider
mapping; `PaymentEvent` MVP `event_type` vocabulary; `attempt_number`
nullable; recovery-eligibility gate before case creation; "at most one
active RecoveryCase per payment" as a business rule.

**Resolved in Phase 1A.2** (ADR-010): the decision-lifecycle contract —
per-action `Prediction` bound to an exact `ModelVersion`; `Prediction` ≠
EIRV ≠ `Recommendation` ≠ `final_action` ≠ `Intervention`; `PolicyEvaluation`
as a distinct per-candidate record with policy version + reason code;
`NO_ACTION` never creates an `Intervention`; `execution_status` vocabulary
(`REQUESTED`/`ACCEPTED`/`REJECTED`/`FAILED`, no `SUCCEEDED`); `Outcome`
(`RECOVERED`/`NOT_RECOVERED`, `observed_at` for delayed outcomes) distinct
from `execution_status` and from `RecoveryCase.status`; multiple immutable
`DecisionRecord`s per case; EIRV inputs/outputs persisted so a historical
decision is explainable without today's model/policy/config;
`feature_schema_version` derived (not stored), `decision_engine_version`
optional metadata.

**Resolved in Phase 1A.3** (ADR-011): full `ModelVersion` lifecycle,
immutability, reproducibility fields, and the one-`PROMOTED`-per-role
rule; `Policy` version immutability + merchant relationship + the
policy-data-vs-policy-engine distinction; `ExperimentAssignment`
assignment level (`RecoveryCase`, immutable), `CONTROL`/`TREATMENT`
semantics (not action-forcing), and its superseding of
`recovery_case.experiment_arm`.

**Resolved in Phase 1A.4** (ADR-012): `TrainingExample` as the ML
observation unit; `DecisionRecord × observed action` granularity;
prediction ≠ observed outcome (no false counterfactual labels); observed
label only for the actually-observed action; `NO_ACTION` observations
without an `Intervention`; repeated-cycle rows; `RecoveryCase` as the
grouping/splitting unit (**case-level** splitting); `Outcome` as the
label; delayed-outcome eligibility; feature-snapshot leakage rule;
failed execution ≠ clean treatment observation; reproducible dataset
snapshot identity.

**Remaining (Phase 1B / ML implementation — NOT data-contract):**

- Physical `training_example` shape/types; the exact dataset-snapshot hash
  mechanism; exact split ratios/seed; physical `Policy` rule
  representation (JSON vs normalized); exact experiment-assignment
  constraint; the "one `PROMOTED` per `model_role`" constraint mechanism.
- Full recovery-eligibility rule set (decision-engine phase).
- Final state-name spelling for the RecoveryCase state machine
  (`ANALYZING` vs `EVALUATING`) — this doc uses `ANALYZING`.
- Whether `REJECTED` vs `FAILED` execution statuses stay split or collapse
  (kept split for now).

## 9. Visual

```
Merchant ──┬── Customer ──── Payment ──── PaymentEvent(s)   [payment lifecycle]
           │                    │
           └── Policy           └── RecoveryCase             [recovery lifecycle]
                                        │ 1:N
                                        ▼
                                 DecisionRecord (1..*, immutable, cycle_number)   [decision lifecycle]
                                        ├── Prediction (1 per candidate action) ──► ModelVersion
                                        ├── PolicyEvaluation (1 per candidate checked)
                                        ├── value context (amount, cost_used + eirv_value per action)
                                        ├── recommended_action / final_action (stored separately)
                                        ├── Intervention (0..1; only RETRY/MESSAGE) ── execution_status   [action/outcome lifecycle]
                                        └── Outcome (0..1; RECOVERED/NOT_RECOVERED, observed_at)
                                               └── (0..1) ── Intervention

RecoveryCase ──0..1──► ExperimentAssignment ◄── Experiment
   (assignment lives on RecoveryCase ONLY, immutable; DecisionRecords inherit it — never their own field)

Merchant ──*──► Policy version (immutable per version)
ModelVersion: immutable except status ∈ {DRAFT, VALIDATED, PROMOTED, RETIRED, REJECTED};
              exactly one PROMOTED per model_role

DecisionRecord ──*──► TrainingExample   [training data — Phase 1A.4]
   (1 per candidate action per cycle; derived + immutable; only the OBSERVED action gets an outcome_label —
    Prediction ≠ observed outcome, no counterfactual labels; grouped by RecoveryCase for case-level splitting)
Training set used to train a ModelVersion = an identifiable/reproducible dataset SNAPSHOT
```
