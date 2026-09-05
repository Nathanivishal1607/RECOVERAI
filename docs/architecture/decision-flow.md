# Decision Flow

## 1. Purpose

Show exactly how one payment failure becomes one action, with the LLM's
role and the policy engine's veto power made unambiguous. This is the most
important document in the repo for defending the product's safety story to
a judge or a skeptical engineer.

## 2. Context

The core non-negotiable principle (see `docs/README.md`): **the LLM never
moves money.** Every financial action passes through deterministic ML
prediction, a deterministic value calculation, and a deterministic policy
check before execution. This document makes that chain concrete.

## 3. Current decision — the full trace

```
1. PAYMENT_FAILED event arrives (normalised PaymentEvent — see data/events.md)
        │
        ▼
2. RECOVERY ELIGIBILITY check (deterministic gate — NOT EIRV)
   supported/ACTIVE merchant? recoverable amount? within recovery window?
   no active case already? policy permits?
        ├── ineligible ──► no RecoveryCase (logged) — trace ends
        │
        ▼ eligible
   RecoveryCase created (status=OPEN)
   [if an experiment is running: ExperimentAssignment made now — ONE per
    case, immutable, CONTROL or TREATMENT — see data/data-model.md]
        │
        ▼
3. Feature extraction (payment, customer, merchant, context)
   — deterministic code, no ML, no LLM
        │
        ▼
4. ML PREDICTION (ml/inference)  →  [Prediction]
   baseline_probability   = P(recover | no action)
   retry_probability      = P(recover | retry)
   message_probability    = P(recover | message)
   [ModelVersion recorded — every prediction is version-stamped; the
    case's experiment arm may select which ModelVersion/strategy is used
    (e.g. TREATMENT → a VALIDATED candidate) but never changes the
    candidate action set or bypasses policy/eligibility]
        │
        ▼
5. INCREMENTAL VALUE ENGINE (deterministic arithmetic, backend/decision_engine)
   incremental(action) = (P(recover|action) - baseline_probability) × amount
                          - cost(action)
        │
        ▼
6. ACTION OPTIMIZER (deterministic, backend/decision_engine)  →  [Recommendation]
   recommended_action = argmax over {retry, message, no_action} of incremental(action)
        │
        ▼
7. POLICY ENGINE (deterministic rules, backend/policies) — CAN VETO
   writes one PolicyEvaluation per candidate checked:
     { action, policy_id, policy_version, result, reason_code, reason, evaluated_at }
        │
        ├── BLOCKED ──► try next-best action (repeat until one passes,
        │               or all fail → final_action = NO_ACTION)
        │
        ▼ ALLOWED
8. DECISION RECORD written (backend/decision_engine)  →  [Decision]  (one per cycle, immutable)
   Predictions: one per candidate action, each with recovery_probability + exact model_version_id
   value context: { cost_used, eirv_value } per action, payment_amount_at_decision
   recommended_action AND final_action (stored SEPARATELY; may differ), decision_reason,
   cycle_number, policy_version_ref
        │
        ▼
9. ACTION GATEWAY executes final_action  →  [Execution]
   only if final_action ∈ {RETRY, MESSAGE} → writes Intervention with
     execution_status ∈ {REQUESTED, ACCEPTED, REJECTED, FAILED}   (no SUCCEEDED)
   final_action = NO_ACTION ⇒ NO Intervention
   RETRY   → Razorpay retry API (Phase 8)
   MESSAGE → message gateway (simulated in MVP; real WhatsApp/SMS/Email in Phase 10)
   (VOICE is a post-MVP action — see integrations/voice.md)
        │
        ▼
10. Outcome recorded when known (result ∈ {RECOVERED, NOT_RECOVERED}, observed_at —
    may lag execution; distinct from execution_status and from RecoveryCase.status)
    → feeds learning loop (see ml/learning-loop.md): each cycle yields
    TrainingExample rows, one per candidate action, but an outcome_label
    is written ONLY for the action actually observed this cycle — the
    other candidates stay predictions, not counterfactual labels.
    Re-evaluation makes a NEW DecisionRecord (higher cycle_number); D1 is
    never mutated.
```

### Where the LLM participates (side channel, never in the chain above)

```
At step 3-4 (optional, Phase 9): LLM may be asked to summarize/explain
   "why is this payment at risk" from the *already privacy-filtered*
   feature snapshot — for display to the merchant, not as model input.

At step 9 (optional, Phase 9-10): if final_action = MESSAGE, LLM drafts the
   customer-facing message text from privacy-filtered fields. The LLM
   does NOT decide whether to send it, when to send it, or to whom —
   those are already fixed by steps 5-8 (and written to the DecisionRecord)
   before the LLM is ever called.

At any time (Phase 11 dashboard): LLM may generate a human-readable
   explanation of a past decision from the stored ModelPrediction +
   policy log — read-only, after the fact.
```

**Implemented (Phase 12A; model selection Phase 12B/12C):** the "at any
time" case above, using NVIDIA NIM's OpenAI-compatible endpoint. Default
model (Phase 12C): **`openai/gpt-oss-20b`**, selected by live-testing
several NVIDIA NIM candidates against the real explanation workload —
reliable ~10-14s structured-JSON responses. (Phase 12B's
`moonshotai/kimi-k3` measured 100s+ per request — a reasoning model that
spends its token budget "thinking" before answering — and was swapped
out; not every model in NVIDIA's public catalog is available on every
account, so several other candidates 404'd immediately.) The model is
read only from `NVIDIA_NIM_MODEL`, never hardcoded.
`GET /api/recovery-cases/{id}/explanation`
(`backend/api/routes/dashboard.py`) builds a structured, privacy-filtered
context from the already-persisted `DecisionAuditRead`
(`backend/services/explanation.py::build_decision_context` — no raw
`feature_snapshot`, no simulator ground truth, no PII), sends it through
`backend/integrations/llm_provider.py::NvidiaNimProvider` (single
non-streaming request; only the final message content is returned, never
a reasoning trace), and validates the response into a fixed
`DecisionExplanation` contract. The system prompt explicitly tells the
model it did not make the decision and must not claim to have computed a
probability, EIRV, authorized an action, executed an intervention, or
observed an outcome independently. Any failure (disabled, unconfigured,
network, timeout, malformed response) degrades to `available: false` —
the decision APIs never depend on this path.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| LLM proposes the action directly (e.g. "the agent decides to retry") | Removes the auditable, reproducible, deterministic core the entire product's safety pitch depends on. An LLM's action choice cannot be reliably reproduced or defended in an audit trail the way a versioned model + deterministic formula can. |
| Policy engine runs *before* ML prediction | Wasteful (would need to evaluate policy for actions that ML wouldn't even recommend) and loses the useful "next-best allowed action" fallback described in UC-4. |
| Policy engine can be overridden by a high enough predicted value | Explicitly rejected per the non-negotiable principle "never bypass policy because the model predicts higher revenue" (see instructions, section 13). |

## 5. Why this option

This is the only design that lets us truthfully claim, in the pitch and in
code, that every money-moving action was (a) predicted by a versioned ML
model (`ModelVersion`), (b) evaluated by a transparent formula, (c) checked
against explicit, inspectable rules, and (d) recorded in a `DecisionRecord`
that preserves what was recommended vs. what was executed — with the LLM
confined to a role it's actually good at (language, explanation) and kept
out of a role it's bad at (auditable, reproducible financial authority).

## 6. Example

```
Case RC-10281: ₹5,000, UPI timeout, attempt 1
  Predictions (each stamped with model_version_id = recovery-v1):
    baseline_probability (NO_ACTION) = 0.28
    retry_probability    = 0.51 → EIRV ≈ ₹1,130
    message_probability  = 0.67 → EIRV ≈ ₹1,930   (highest)
  Recommendation:  recommended_action = MESSAGE (EIRV ₹1,930)
  Policy check on MESSAGE: contact count 0/2 ✓, channel enabled ✓ → ALLOWED
  DecisionRecord: { recommended_action: MESSAGE, policy_result: ALLOWED,
                    final_action: MESSAGE }
                    -- model version is NOT a DecisionRecord column; it is
                    -- derived from the linked Predictions (all recovery-v1 here)
  Execution: MESSAGE via message gateway → Intervention row written; LLM
    drafts the text from {amount, method, failure_category, customer_type}
    only (no name/phone/card)

Contrast — same case but contact count already 2/2:
  Recommendation:  MESSAGE      Policy: BLOCKED (max contacts)
  DecisionRecord: { recommended_action: MESSAGE, policy_result: BLOCKED,
                    final_action: NO_ACTION, decision_reason: "contact limit" }
  Execution: none. The recommendation is still recorded.
```

## 7. Implementation implications

- Step 7's "remove candidate and retry step 6" logic must be implemented as
  a loop with a guaranteed terminating case (`NO_ACTION` always passes
  policy) — see `decision-engine/policy-engine.md`.
- The LLM call, if enabled, must never be on the critical path for deciding
  *whether* to act — it can run in parallel with, or strictly after, the
  decision is already finalized.

## 8. Open questions

- None outstanding; this flow is considered locked. Any proposed change to
  it is an architectural decision requiring an ADR (see
  `decisions/architecture-decisions.md`) before implementation.

## 9. Visual

The trace in section 3 is the canonical diagram for this document.
