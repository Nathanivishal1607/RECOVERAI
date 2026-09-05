# Features

## 1. Purpose

Define the exact feature set fed into the recovery/uplift models, shared
between training (`ml/training/`) and inference (`ml/inference/`) via
`ml/features/` so the two never drift apart.

## 2. Context

Drawn directly from product discussion's feature brainstorm. This document
narrows that brainstorm down to the MVP-relevant set (matching
`product/mvp-scope.md`'s single trigger, a `PAYMENT_FAILED` payment-lifecycle
event that has passed recovery eligibility — see `data/data-model.md`).

## 3. Current decision — MVP feature set

### Payment features

| Feature | Type | Notes |
|---|---|---|
| `amount` | numeric | in smallest currency unit (paise) |
| `payment_method` | categorical | UPI, CARD, NETBANKING, ... |
| `failure_category` | categorical | TIMEOUT, INSUFFICIENT_FUNDS, AUTH_FAILURE, RISK_BLOCK, ABANDONED, OTHER |
| `attempt_number` | numeric | 1, 2, 3... |
| `hour_of_day` | numeric (0-23) | from `created_at` |
| `day_of_week` | categorical | from `created_at` |
| `time_since_failure_minutes` | numeric | at prediction time |

### Customer features (aggregates only — see privacy-architecture.md)

| Feature | Type | Notes |
|---|---|---|
| `transaction_count` | numeric | lifetime |
| `historical_success_rate` | numeric | successful / total |
| `average_transaction_value` | numeric | |
| `days_since_last_payment` | numeric | |
| `historical_recovery_rate` | numeric | of past failed payments, how many recovered |
| `prior_contact_count_window` | numeric | contacts within the policy's contact window — needed for both the model and the policy engine |

### Merchant/context features

| Feature | Type | Notes |
|---|---|---|
| `merchant_category` | categorical | industry |
| `merchant_historical_recovery_rate` | numeric | |

### Action feature (for per-action probability models)

| Feature | Type | Notes |
|---|---|---|
| `candidate_action` | categorical | RETRY, MESSAGE, NO_ACTION — see `ml/models.md` for whether this is a feature in one model or a model-per-action |

## 4. Alternatives considered

Considered including raw text (e.g. gateway error messages) with NLP/embedding
features. Rejected for MVP — the structured `failure_category`/`failure_code`
fields carry equivalent signal with far less complexity; free-text parsing
is a possible future enhancement, not needed now.

Considered including device/IP/graph-based features (relevant to the Fraud
Ring Investigator idea from Track 2 exploration). Explicitly out of scope —
that's a different product; RecoverAI's features stay focused on recovery
likelihood, not fraud/abuse detection.

## 5. Why this option

This set is small enough to implement and simulate credibly within
hackathon time, while covering every signal category product discussion
identified as plausible for recovery prediction (payment context, customer
history, merchant context). It also matches one-to-one with what the
synthetic generator (`data/synthetic-data.md`) needs to produce.

## 6. Example

```json
{
  "amount": 500000,
  "payment_method": "UPI",
  "failure_category": "TIMEOUT",
  "attempt_number": 1,
  "hour_of_day": 23,
  "day_of_week": "Friday",
  "time_since_failure_minutes": 12,
  "transaction_count": 14,
  "historical_success_rate": 0.93,
  "average_transaction_value": 420000,
  "days_since_last_payment": 5,
  "historical_recovery_rate": 0.80,
  "prior_contact_count_window": 0,
  "merchant_category": "SaaS",
  "merchant_historical_recovery_rate": 0.55
}
```

## 7. Implementation implications

- `ml/features/build_features.py` (created in Phase 3) must be the single
  function called both by training data preparation and by the live
  inference path in `backend/services`, to guarantee train/serve
  consistency.
- This feature dict is exactly what gets persisted as
  `ModelPrediction.feature_snapshot` (see `data/database-schema.md`) — the
  audit trail and the model input are the same object, by design.

## 8. Open questions

- Whether `prior_contact_count_window` should be computed from
  `intervention` history directly at inference time or maintained as a
  rolling counter — leaning toward computing it directly (simpler, and
  volumes are small enough for MVP) with revisit if performance requires
  caching.

## 9. Visual

```
Payment + Customer + Merchant + Context
                │
                ▼
        build_features() ── single shared function ──┐
                │                                      │
                ▼                                      ▼
          TRAINING DATA                         LIVE INFERENCE
                                                        │
                                                        ▼
                                          persisted as feature_snapshot
                                          (audit trail)
```
