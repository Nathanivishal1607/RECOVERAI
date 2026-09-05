# Razorpay Integration

## 1. Purpose

Define how RecoverAI integrates with Razorpay, and fix the rule that no
specific API endpoint should be assumed to exist until verified against
current official documentation at implementation time.

## 2. Context

This is a Phase 8 concern — the MVP (Phases 2-7) runs entirely on the
synthetic event feed (`data/synthetic-data.md`, `data/events.md`) precisely
so that core decision-engine correctness doesn't depend on integration
readiness. This document specifies the integration's *shape*, deferring
exact endpoint names/parameters to implementation time.

## 3. Current decision

### What we need from Razorpay (functionally, not by specific endpoint name)

```
1. Webhook events for payment lifecycle (failed, retried, captured) —
   see integrations/webhooks.md for signature verification requirements.
2. A way to query current payment/order status.
3. A way to trigger a retry-eligible action or generate a fresh payment
   link/checkout session for a failed payment, if and where Razorpay's
   current API supports this for the merchant's payment methods.
4. Test-mode credentials for development and for the Buildathon demo,
   consistent with Razorpay's own test-mode tooling.
```

### What we explicitly do NOT assume

```
- That a specific "retry payment" API endpoint exists with a particular
  name/shape — this must be verified against current official Razorpay API
  docs before backend/integrations/razorpay_client.py is implemented.
- That subscription/mandate-specific APIs are needed — out of MVP scope
  per product/mvp-scope.md.
```

### Integration boundary

```
backend/integrations/razorpay_client.py
   - thin wrapper around verified Razorpay SDK/HTTP calls
   - the ONLY place in the codebase allowed to call Razorpay directly
   - called only by backend/integrations' action-gateway code, itself only
     invoked AFTER the decision engine + policy engine have finalized the
     decision (the DecisionRecord) — see architecture/decision-flow.md step 9
```

## 4. Alternatives considered

Considered building against assumed/remembered Razorpay API shapes from
general knowledge to move faster. Rejected per explicit instruction
(section 5: "Do not assume an API endpoint exists. Verify the current
official Razorpay API documentation before implementing an integration.")
— API surfaces change, and an incorrect assumption here would silently
break the one part of the system that touches real money movement.

## 5. Why this option

Keeping Razorpay calls behind one thin, isolated client module means (a)
verifying/updating against current docs only touches one file, and (b) the
decision engine and policy engine can be fully built, tested, and
demonstrated (Phases 2-7) without this integration existing yet, which
protects the project timeline against any Razorpay API research taking
longer than expected.

## 6. Example

Deferred — no concrete request/response example should be written here
until real endpoint verification happens in Phase 8, to avoid this
document silently becoming a source of incorrect assumptions.

## 7. Implementation implications

- Phase 8's first task is API research (against current official Razorpay
  docs), not coding — the result of that research updates this document
  before `razorpay_client.py` is written.
- `integrations/webhooks.md` covers the inbound side; this document covers
  outbound (action-triggering) calls.

## 8. Open questions

- Exact mechanism for triggering a "retry" — whether this is a Razorpay-
  native capability, or whether "retry" in RecoverAI's action set actually
  means "generate and send a fresh payment link/checkout session" via a
  verified Razorpay API — to be resolved in Phase 8 research before
  implementation.
- What test-mode data/sandbox Razorpay provides for the Buildathon
  specifically, and whether it's sufficient for a live demo or whether the
  demo should run entirely on the synthetic feed with Razorpay integration
  shown as a secondary/optional live segment.

## 9. Visual

```
   Decision Engine + Policy Engine (already finalized decision)
                    │
                    ▼
         backend/integrations/razorpay_client.py
          (ONLY module allowed to call Razorpay)
                    │
                    ▼
              Razorpay API (test mode)
```
