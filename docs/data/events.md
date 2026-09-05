# Events

## 1. Purpose

Enumerate the event types the system consumes and produces, so ingestion
code (real or synthetic) has a fixed contract to implement against.

## 2. Context

Events are the system's entry point (`PaymentEvent`, from Razorpay webhooks
or the synthetic generator) and one of its outputs (status transitions,
outcomes) that feed the dashboard and learning loop.

## 3. Current decision

### Internal `PaymentEvent` vocabulary (what we persist)

Inbound signals — from Razorpay webhooks (Phase 8) or the synthetic
generator (Phase 2–7) — are normalised into a small controlled
`PaymentEvent.event_type` vocabulary. This is the authoritative internal
contract (see `data/data-model.md` "Core data contract — Phase 1A.1"):

| `event_type` | Meaning | `attempt_number` |
|---|---|---|
| `PAYMENT_CREATED` | payment initiated | `NULL` |
| `PAYMENT_FAILED` | an attempt failed — **the recovery trigger** (subject to eligibility) | set |
| `RETRY_ATTEMPTED` | a new attempt was initiated (by the customer or by RecoverAI) | set |
| `PAYMENT_SUCCEEDED` | payment ultimately succeeded — closes a linked case as `RECOVERED` | set |
| `PAYMENT_CANCELLED` | abandoned / cancelled, no further attempts expected | `NULL` or set |

Smallest set that supports the recovery use case. `PAYMENT_PROCESSING` /
`PAYMENT_AUTHORIZED` are **not** distinct events (they update
`Payment.status` to `PROCESSING` and/or land in `metadata`);
`PAYMENT_CAPTURED` is folded into `PAYMENT_SUCCEEDED`. New event types are
added only for a concrete recovery/simulator/adapter need — see
`data/data-model.md` for the rationale.

`PaymentEvent` rows are **immutable and append-only** — never updated or
deleted; a correction is a new event. `PaymentEvent` is the authoritative
chronological record; `Payment.status` is a derived convenience. A single
`Payment` can carry many `PaymentEvent`s across attempts even though the
MVP maps it to at most one active `RecoveryCase`.

### Provider webhook → internal mapping (illustrative)

Exact Razorpay event/field names are confirmed at Phase 8 (see
`integrations/webhooks.md`, `integrations/razorpay.md`).

| Razorpay webhook (examples) | Internal `event_type` | MVP? |
|---|---|---|
| `payment.failed` | `PAYMENT_FAILED` | ✅ |
| `payment.captured`, `order.paid` | `PAYMENT_SUCCEEDED` | ✅ |
| retry we trigger / `payment.created` on a re-attempt | `RETRY_ATTEMPTED` | ✅ |
| `payment.created` (first attempt) | `PAYMENT_CREATED` | ✅ |
| `payment.authorized` / pending | *(status → `PROCESSING`; no distinct event)* | logged only |
| payment voided / cancelled | `PAYMENT_CANCELLED` | ✅ |
| `checkout.abandoned` | *(post-MVP — different case type)* | ❌ |
| `subscription.charged.failed`, `invoice.overdue` | *(post-MVP)* | ❌ |

### A `PAYMENT_FAILED` event does not automatically create a case

Case creation goes through a **recovery eligibility** gate:

```
PAYMENT_FAILED  ──►  recovery eligibility check  ──►  eligible?  ──►  open RecoveryCase (status OPEN)
                                                       │
                                                       └── ineligible ──►  no case (logged)
```

Eligibility ("should this payment enter the recovery system?") is a
distinct stage from EIRV ("which action is best for a case that exists").
See `data/data-model.md` "Recovery eligibility" and
`decision-engine/decision-engine.md`.

### Internal/derived events (system-generated, drive status transitions)

```
case.created            (→ OPEN)
case.analyzing          (features + prediction begin, → ANALYZING)
case.decided            (DecisionRecord finalized, → ACTION_SELECTED)
case.action_blocked     (policy vetoed the recommended action — includes reason;
                         recorded on the DecisionRecord, recommendation ≠ final action)
case.action_executed    (→ ACTION_EXECUTED)
case.waiting_for_outcome (→ WAITING_FOR_OUTCOME)
case.outcome_recorded
case.recovered          (terminal)
case.stopped            (stopping rule ended the case — terminal)
case.expired            (resolution window elapsed — terminal)
case.failed             (unrecoverable system/execution error — terminal)
```

These map to the `RecoveryCase` state machine in `data/data-model.md` and
are recorded via `recovery_case_status_history` (see
`data/database-schema.md`) rather than a separate event bus for MVP — see
Alternatives below.

**Four separate lifecycles (do not collapse — Phase 1A.2 / ADR-010):**

| Lifecycle | Record | This doc |
|---|---|---|
| Payment | `Payment` → `PaymentEvent`s (append-only) | ← here |
| Recovery | `RecoveryCase` status machine | `data/data-model.md` |
| Decision | `DecisionRecord` → `Prediction`s / EIRV / recommendation / `PolicyEvaluation` / `final_action` | `data/data-model.md` |
| Action/Outcome | `Intervention` → `execution_status` → `Outcome` | `data/data-model.md` |

`DecisionRecord` does **not** duplicate `PaymentEvent`s — payment
lifecycle events stay here. A payment can accrue several `PaymentEvent`s
while its single MVP `RecoveryCase` runs multiple decision cycles (each a
new, immutable `DecisionRecord`).

## 4. Alternatives considered

Considered publishing internal events onto a Redis Streams/Celery event bus
that other services subscribe to (a more "event-driven microservices" style).
Rejected for MVP: with a single backend service, a directly-written status
history table achieves the same auditability with far less operational
complexity. This may be revisited if/when the backend is split into
multiple services — not currently planned.

## 5. Why this option

Keeping the internal `PaymentEvent` vocabulary minimal (5 values) keeps
`ml/features.md` and the decision engine focused, per `product/mvp-scope.md`.
Normalising provider events into our own vocabulary (rather than passing
Razorpay's raw event names through) means the simulator, backend, and the
future provider adapter all speak one contract, and provider-specific
detail stays in `metadata` / `provider_event_id`.

## 6. Example

Inbound signal (provider or synthetic), before normalisation:

```json
{
  "event_type": "payment.failed",
  "payload": {
    "payment_display_id": "P-78291",
    "merchant_display_id": "M-019",
    "customer_id": "C-482",
    "amount": 500000,           // paise, matching Razorpay's convention
    "currency": "INR",
    "method": "upi",
    "error_code": "BAD_REQUEST_ERROR",
    "error_description": "Payment timed out"
  }
}
```

Normalised `PaymentEvent` we persist:

```json
{
  "event_type": "PAYMENT_FAILED",
  "payment_id": "<payment uuid>",
  "event_timestamp": "2026-09-01T18:42:11Z",
  "attempt_number": 1,
  "amount": "5000.00",
  "currency": "INR",
  "provider_event_id": "evt_...",
  "metadata": { "error_code": "BAD_REQUEST_ERROR", "method": "upi" },
  "created_at": "2026-09-01T18:42:13Z"
}
```

## 7. Implementation implications

- The synthetic generator (Phase 2) must emit inbound signals that
  normalise to the same `PaymentEvent` vocabulary and `attempt_number`
  semantics as real Razorpay webhooks (Phase 8), so swapping the source
  requires no change to downstream code.
- The normalisation step (inbound signal → `PaymentEvent`) and the
  recovery-eligibility gate are the two ingestion stages both sources
  share.
- Money in `PaymentEvent`/`Payment` is an exact decimal (see
  `data/data-model.md`); provider amounts arriving in paise are converted
  on ingestion, not stored as floats.

## 8. Open questions

- Exact mapping from Razorpay's real webhook error codes to our
  `failure_category` taxonomy (TIMEOUT, INSUFFICIENT_FUNDS, AUTH_FAILURE,
  RISK_BLOCK, ABANDONED, OTHER) needs to be finalized against current
  official Razorpay documentation at Phase 8 implementation time — do not
  assume the mapping above is complete.

## 9. Visual

```
Razorpay webhook (Phase 8)  ─┐
                              ├──► normalise to PaymentEvent ──► recovery eligibility ──► (if eligible) RecoveryCase
Synthetic generator (Ph 2-7) ─┘        (5-value vocabulary)          (a gate, not automatic)
```
