# Webhooks

## 1. Purpose

Define how RecoverAI receives real Razorpay events, and lock the security
requirements (signature verification, idempotency) that must never be
skipped even temporarily for demo convenience.

## 2. Context

This is the inbound half of the Razorpay integration (Phase 8), feeding the
same ingestion contract described in `data/events.md` that the synthetic
generator uses for Phases 2-7 — the goal is that swapping sources requires
no downstream code change.

## 3. Current decision

### Endpoint

```
POST /webhooks/razorpay   (backend/api)
```

### Processing steps (all mandatory, in order)

```
1. Read raw request body (needed for signature verification — must happen
   BEFORE any JSON parsing that might alter byte representation).
2. Verify the webhook signature using RAZORPAY_WEBHOOK_SECRET
   (see .env.example) against Razorpay's current documented signature
   scheme — verified at implementation time, not assumed.
3. Reject (4xx, no processing) if signature verification fails. This step
   is never skipped, including in development — use real test-mode webhook
   secrets locally, never a bypass flag.
4. Check idempotency: has this event id already been processed?
   (Redis-backed set with TTL, or a unique constraint on event id in
   payment_event — event delivery may be retried by Razorpay, and this
   must not create duplicate RecoveryCases or duplicate interventions.)
5. If new: translate the payload into our internal event shape
   (data/events.md) and hand off to the same ingestion path the synthetic
   generator uses.
6. Return 200 quickly; do the actual decision-engine work asynchronously
   (via Celery/Redis, see architecture/system-architecture.md) so Razorpay
   doesn't experience a slow webhook response.
```

## 4. Alternatives considered

Considered processing webhooks synchronously within the request handler for
simplicity. Rejected — webhook providers commonly expect fast responses and
may retry on timeout; doing the full predict→decide→policy→act pipeline
synchronously risks both slow responses and duplicate processing on retry.
Async handoff with idempotency is the standard, safer pattern.

## 5. Why this option

This design directly reuses the ingestion contract already defined for the
synthetic data path (`data/events.md`), meaning Phase 8 is primarily "add a
new event source," not "build a new pipeline" — consistent with the
system-architecture goal of the synthetic and real paths being
interchangeable.

## 6. Example

```
Inbound webhook (illustrative shape — verify exact fields at Phase 8):
{
  "event": "payment.failed",
  "payload": {
    "payment": {
      "entity": { "id": "pay_...", "amount": 500000, "method": "upi",
                  "error_code": "BAD_REQUEST_ERROR", ... }
    }
  }
}
→ signature verified → idempotency check (new) → normalised to an internal
  PaymentEvent (`PAYMENT_FAILED`, 5-value vocabulary — data/events.md) →
  recovery eligibility check → RecoveryCase opened *only if eligible* →
  200 OK returned → async processing continues
```

Note: a `payment.failed` webhook does not automatically create a
RecoveryCase — it becomes a `PAYMENT_FAILED` `PaymentEvent` and passes the
recovery-eligibility gate first (see `data/data-model.md`).

## 7. Implementation implications

- Signature verification logic belongs in `backend/integrations/razorpay_webhooks.py`,
  tested with both valid and deliberately-tampered payloads (Phase 8 tests).
- Idempotency keys should be namespaced (e.g. `webhook:razorpay:{event_id}`)
  to avoid collision with other Redis usage (queues, rate limits).

## 8. Open questions

- Exact current Razorpay webhook payload shape and signature header name —
  to be confirmed against official docs at Phase 8 implementation time
  (explicitly not assumed here, per `integrations/razorpay.md`).

## 9. Visual

```
Razorpay ──► POST /webhooks/razorpay
                   │
                   ▼
          verify signature ──(fail)──► 4xx, drop
                   │ (pass)
                   ▼
          idempotency check ──(duplicate)──► 200, no-op
                   │ (new)
                   ▼
          translate → internal event shape (data/events.md)
                   │
                   ▼
          200 OK returned; async decision pipeline continues
```
