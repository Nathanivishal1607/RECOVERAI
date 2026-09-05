# Use Cases

## 1. Purpose

Enumerate concrete scenarios the system must handle, ordered by priority, so
implementation phases have unambiguous acceptance targets.

## 2. Context

Derived from the problem statement (`problem-statement.md`) and the MVP
scope (`mvp-scope.md`). Each use case below maps to the core loop:
event → prediction → incremental value → decision → policy → action →
outcome → learning.

## 3. Current decision — use case list

### UC-1 (MVP, core): Single failed payment, decide and act

**Actor:** System, triggered by a `PAYMENT_FAILED` payment-lifecycle event.
**Flow:**
1. Recovery eligibility check → if eligible, a RecoveryCase is opened (if
   ineligible, no case — logged).
2. Features extracted (payment, customer, merchant, context).
3. Baseline + per-action recovery probabilities predicted.
4. Incremental value computed per candidate action (retry, message, none).
5. Policy engine filters disallowed actions.
6. A DecisionRecord is written (recommended vs final action); the final
   action is executed (or "no action").
7. Outcome recorded when known.
**Acceptance:** Every *eligible* failed payment produces a RecoveryCase
with a recorded prediction, a DecisionRecord, a policy-checked decision,
and (eventually) an outcome. Ineligible failures are logged, not silently
dropped.

### UC-2 (MVP): Merchant reviews a decision

**Actor:** Merchant operator, via dashboard.
**Flow:** Operator opens a case and sees the amount, failure reason, all
predicted probabilities, the `DecisionRecord` (recommended action + EIRV,
policy result, final action — with the recommended and final action shown
distinctly when they differ), and (if resolved) the outcome.
**Acceptance:** Nothing in the decision is a black box — every number shown
was produced by a named `ModelVersion` and a named policy rule set, and the
recommendation-vs-execution distinction is visible.

### UC-3 (MVP): Baseline vs. RecoverAI comparison

**Actor:** Merchant operator / evaluator.
**Flow:** Run the same batch of historical failed payments through (a) a
fixed baseline strategy (e.g. "retry everyone once") and (b) RecoverAI.
Compare recovered revenue, incremental revenue, and intervention count.
**Acceptance:** Dashboard produces a side-by-side comparison with a single
incremental-revenue headline number.

### UC-4 (MVP): Policy blocks the recommended action

**Actor:** System.
**Flow:** The optimizer's `recommended_action` is `MESSAGE` (highest EIRV),
but the customer's contact count for the window is already at the
merchant's limit (or the merchant has `MESSAGE` disabled) → policy engine
blocks it → the next-best allowed action (`RETRY` or `NO_ACTION`) becomes
`final_action`. The `DecisionRecord` stores both, plus the block reason.
(Post-MVP, the same mechanism blocks `VOICE` when the merchant has it
disabled or there is no consent on file.)
**Acceptance:** A blocked recommendation is never silently dropped — the
`DecisionRecord` shows `recommended_action ≠ final_action` with the reason.

### UC-5 (MVP): Do-nothing is chosen

**Actor:** System.
**Flow:** For a low-amount, low-recovery-probability, already-contacted-
multiple-times case, every intervention's incremental value is negative or
below a minimum threshold → system chooses "no action."
**Acceptance:** This is displayed as a deliberate decision with its
reasoning, not treated as "system did nothing" / an error state.

### UC-6 (Phase 7 — learning loop): Model improves from new outcomes

**Actor:** System (scheduled/batch).
**Flow:** N new outcomes accumulate → new candidate `ModelVersion`
(`DRAFT`) trained → evaluated against the current `PROMOTED` model for its
role on a held-out set → `VALIDATED` then `PROMOTED` only if actually
better, else `REJECTED` (which can never later become `PROMOTED` — see
ADR-011). At most one `PROMOTED` `ModelVersion` per model role.
**Acceptance:** Dashboard can show "model v2 trained on N new cases,
improved [metric] from X to Y, promoted on [date]."

### UC-7 (extension, post-MVP): real message-channel / voice intervention

**Actor:** System + external customer.
**Flow:** Chosen action is a real outbound `MESSAGE` through an actual
channel behind the message gateway (WhatsApp / SMS / Email), or a `VOICE`
call (Sarvam), with generated content passing through the privacy filter
before reaching the LLM, and the policy engine's consent/contact-limit
checks before sending.
**Acceptance:** Core decision engine must work identically whether or not
this extension is enabled — its absence must never break UC-1.

### UC-8 (extension, post-MVP): Checkout abandonment / subscription failure

**Actor:** System.
**Flow:** Same decision engine, different event type and feature set.
**Acceptance:** Explicitly out of MVP; only pursued if UC-1–UC-6 are solid.

## 4. Alternatives considered

Considered making voice/WhatsApp (UC-7) part of the MVP to maximize demo
"wow factor." Rejected — see `mvp-scope.md` for the reasoning (core engine
must be provably solid before any channel extension is added).

## 5. Why this set and ordering

This ordering directly mirrors the phase list in `docs/README.md` and
ensures that at every phase boundary there is a demonstrable, testable
increment rather than a pile of half-finished features.

## 6. Example

See UC-1 example fully worked through in `product/product-overview.md`
section 6.

## 7. Implementation implications

- UC-3 (baseline comparison) must be buildable from Phase 2 (synthetic data)
  onward — it doesn't require the full backend, just the simulator + models.
  This should be one of the earliest end-to-end vertical slices.
- UC-4 and UC-5 both require the policy engine and decision engine to log
  *counterfactual* information (what would have been chosen absent the
  block/threshold), which affects the `ModelPrediction` and `Intervention`
  schemas — see `data/database-schema.md`.

## 8. Open questions

- Exact minimum-value threshold for "do nothing" (UC-5) — to be tuned
  empirically once the simulator and baseline model exist (Phase 3/5).

## 9. Visual — use case coverage across phases

```
Phase 2 (sim) ──► UC-3 (baseline vs RecoverAI, offline)
Phase 3-5     ──► UC-1, UC-4, UC-5 (prediction + decision + policy)
Phase 6       ──► UC-1 end-to-end via real API/DB
Phase 7       ──► UC-6 (learning loop)
Phase 8       ──► UC-1 driven by real Razorpay webhooks
Phase 10      ──► UC-7 (channels)
Phase 11      ──► UC-2 (dashboard) becomes fully real
(post-MVP)    ──► UC-8
```
