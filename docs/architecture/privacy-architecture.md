# Privacy Architecture

## 1. Purpose

Define, precisely and checkably, what data is allowed to reach the LLM (and
any other external service) versus what stays internal — turning "privacy
by design" from a stated value into an enforceable boundary.

## 2. Context

This requirement originated directly from product discussion: because the
system handles money and customer data together, every AI-assisted decision
must be demonstrably correct and privacy-respecting, not just effective.
Razorpay's Buildathon brief itself repeatedly emphasizes bounded, gated,
auditable actions — privacy minimization is part of satisfying that bar,
not a separate concern.

## 3. Current decision

### Data classification

| Class | Examples | May reach LLM? | May reach 3rd-party channel provider? |
|---|---|---|---|
| Highly sensitive | card number, CVV, bank account/IFSC, raw API keys/secrets | Never | Never |
| Sensitive / PII | customer name, phone, email, physical address | Never (use internal `customer_id` instead) | Only the minimum needed to actually deliver a message (e.g. phone number to the messaging provider, not to the LLM) |
| Internal / behavioral | transaction history aggregates, recovery rate, attempt count | Only as aggregated/derived features, never raw event dumps | No |
| Task-scoped derived fields | `customer_type: returning`, `amount: 5000`, `payment_method: UPI`, `failure_category: timeout`, `previous_success_rate: 0.91` | Yes — this is the intended LLM input shape | N/A |
| Public / non-sensitive | product category, merchant industry | Yes | Yes |

### The privacy gateway

```
                 RAW INTERNAL DATA (Postgres)
                            │
                            ▼
                 ┌───────────────────────┐
                 │   PRIVACY GATEWAY     │
                 │ (backend/services/    │
                 │  privacy_filter.py)   │
                 └───────────┬───────────┘
                            │
              ┌──────────────┴───────────────┐
              ▼                               ▼
      Task-scoped, minimized             BLOCKED
      fields only                        (highly sensitive / unneeded PII)
              │
              ▼
            LLM
```

Every call site that sends data to the LLM (or logs a prompt) must construct
its payload through a single, named allow-listed function — never by
passing an ORM object or a raw dict pulled straight from the database. This
is the enforceable part of the design: a code reviewer can grep for direct
LLM calls and verify each one goes through the filter.

### The LLM must never receive

Stated explicitly so it cannot be softened later:

```
- card numbers
- CVV
- bank credentials / account / IFSC
- API keys
- authentication secrets
- unnecessary PII (name, phone, email, address)
```

Internal identifiers (e.g. `customer_id`) are always preferred over
unnecessary customer identity information. The LLM receives only the
minimum information required for its specific task — nothing more.

### Example transformation

```python
# Internal record (never sent as-is)
{
  "customer_id": "C-482",
  "name": "Vikas Kumar",
  "phone": "98XXXXXXXX",
  "email": "vikas@example.com",
  "upi_vpa": "vikas@okhdfc",
  "card_last4": "4242",
  "amount": 47500,
  "payment_status": "failed",
  "failure_code": "BAD_REQUEST_ERROR",
  "attempt_count": 2
}

# What the LLM actually receives
{
  "customer_type": "returning",
  "payment_amount": 47500,
  "payment_status": "failed",
  "failure_category": "timeout",
  "attempt_count": 2,
  "previous_success_rate": 0.91
}
```

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Trust each service/call site to manually redact fields | Error-prone; a single missed field (e.g. accidentally including `email`) silently violates the principle with no enforcement mechanism. |
| Tokenize PII (reversible) instead of omitting it | Unnecessary complexity for MVP — the LLM's tasks (investigation, explanation, message drafting) don't actually need to resolve tokens back to identity; straightforward omission is simpler and safer. |
| Redact via regex/PII-detection scanning of free text | Relevant for OCR/document-based extensions if pursued later (e.g. chargeback evidence, out of current scope), but our structured data doesn't need probabilistic redaction — an explicit allow-list of fields is stricter and simpler. |

## 5. Why this option

An allow-list (only named, task-scoped fields ever leave the boundary) is
strictly safer than a deny-list (block known-bad fields, allow everything
else by default) — a new column added to the `Customer` table in the future
cannot accidentally leak through an allow-list-based filter, whereas it
could through a deny-list.

## 6. Example

See section 3's worked transformation. Additional worked example for a
message-drafting call:

```
Task: draft a recovery MESSAGE (channel-agnostic; WhatsApp/SMS/Email post-MVP)
Input to LLM: {payment_amount: 5000, payment_method: "UPI",
               failure_category: "timeout", customer_type: "returning"}
LLM output: "Hi! Your ₹5,000 payment didn't go through due to a network
             timeout — no charge was made. Tap here to try again: [LINK]"
Note: LLM never sees the customer's name/phone; the messaging provider
      (not the LLM) is the only component with the phone number, and only
      at final send time.
```

## 7. Implementation implications

- `backend/services/privacy_filter.py` (created in a later phase, not
  Phase 0) will define one function per LLM task
  (`build_investigation_context`, `build_message_drafting_context`, etc.),
  each with an explicit, reviewable field allow-list.
- Secrets (API keys, DB credentials) live only in environment variables
  (`.env`, never committed) and are never part of any LLM prompt or logged
  payload — see `.env.example` and `development/environment.md`.
- Unit tests (Phase 9) should assert that the constructed LLM payload for
  each task contains only allow-listed keys.
- The audit trail (`architecture/security-and-safety.md` section C) is
  generated by the system from structured records (`Prediction`,
  `DecisionRecord`, `PolicyEvaluation`, `Intervention`, `Outcome`,
  `ModelVersion`, `Policy` version, `ExperimentAssignment`). The LLM
  never generates or determines the authoritative audit record — at most
  it *rephrases* already-persisted records into prose for display.
- `ModelVersion`, `Policy`, `Experiment`, and `ExperimentAssignment`
  (Phase 1A.3) hold configuration/metadata and internal references only
  (`merchant_id`, `recovery_case_id`, `model_version_id`, …) — no
  customer/payment-sensitive data is duplicated into them.

## 8. Open questions

- Whether merchant-configured custom fields (a future extensibility idea)
  would need per-merchant allow-list configuration — deferred; not needed
  for MVP's fixed field set.

## 9. Visual

See the privacy gateway diagram in section 3.
