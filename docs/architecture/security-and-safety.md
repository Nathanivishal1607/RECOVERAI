# Security and Financial Safety

## 1. Purpose

Define the concrete mechanisms that keep RecoverAI from taking a harmful,
unauthorized, or excessive action — separate from privacy (`privacy-architecture.md`),
which governs *what data* is exposed, not *what actions* are allowed.

## 2. Context

Because RecoverAI can trigger customer-facing actions (retries, messages,
eventually calls) and reads/writes payment-related data, both the actions
and the data pipeline need explicit safety controls. Razorpay's Track 3 brief
explicitly requires "compliant escalation, stopping rules, and an audit
trail" — this document is where those requirements become concrete.

## 3. Current decision

### A. Action safety (financial/customer-facing)

Enforced entirely by the Policy Engine (`decision-engine/policy-engine.md`),
which every action must pass through. Core rules for MVP:

```
- max_retry_count per RecoveryCase           (default: 2)
- max_customer_contacts per time window      (default: 2 per 7 days)
- allowed_intervention_types per merchant    (merchant-configurable)
- amount limits for autonomous action        (above threshold → escalate/hold)
- risk flag present on payment               (→ no autonomous action, escalate)
- consent requirement for voice channel      (Phase 10 — hard block without consent)
```

Any action that fails a check is not executed; the optimizer is asked for
the next-best action excluding the blocked one; if no action passes,
`NO_ACTION` is the (always-passing) fallback (see `decision-flow.md`).

### B. Stopping rules

A case stops (decision becomes `NO_ACTION`, no further automated action)
when any of these holds — see `decision-engine/decision-engine.md` for
where this is checked in the decision flow:

```
- Maximum retry attempts reached
- Maximum contact attempts reached
- No positive expected incremental value for any allowed action
- Customer has opted out
- Policy restriction (no allowed action remains)
- Recovery already completed
- Case expired (configurable time window, default 14 days, with no recovery)
```

Backstop behavior (see the `RecoveryCase` state machine in
`data/data-model.md`): a case moves to `STOPPED` once `max_retry_count`
AND `max_customer_contacts` are both reached (or any other stopping
condition holds), and to `EXPIRED` once the resolution time window elapses
with no recovery. A case with a declining/negative recomputed incremental
value on successive attempts also stops rather than continuing to
intervene. These are documentation-level requirements at this phase, not
yet implemented.

### C. Audit trail

For every decision (one `DecisionRecord`), the system must be able to
answer, from structured records alone (Phase 1A.2 / ADR-010):

```
 1. Which RecoveryCase?                          DecisionRecord.recovery_case_id
 2. Which payment?                               → via RecoveryCase.payment_id
 3. Which decision cycle?                         DecisionRecord.cycle_number
 4. When was the decision made?                   DecisionRecord.decision_timestamp
 5. What candidate actions were evaluated?        the Prediction set (RETRY/MESSAGE/NO_ACTION)
 6. What did the model predict for each?          Prediction.recovery_probability per action
 7. Which ModelVersion produced those?            Prediction.model_version_id (exact, immutable)
 8. What economic values were calculated?         value context: cost_used + eirv_value per action, amount
 9. Which action was recommended?                 DecisionRecord.recommended_action
10. Which policy / version was evaluated?          PolicyEvaluation.policy_id + policy_version
11. Was the recommendation allowed?               PolicyEvaluation.result for that action
12. If blocked, why?                              PolicyEvaluation.reason_code + reason
13. What was the final action?                    DecisionRecord.final_action  (may ≠ recommended)
14. Was an intervention executed?                 Intervention present? (NULL for NO_ACTION)
15. What was the execution result?                Intervention.execution_status  (≠ outcome)
16. What outcome occurred?                        Outcome.result (+ recovery_amount)
17. When was that outcome observed?               Outcome.observed_at  (may lag execution)
18. What happened to the RecoveryCase afterward?  RecoveryCase.status + history / next DecisionRecord
```

The `DecisionRecord` links these structured records; it is **not** one
opaque JSON blob. Every RecoveryCase persists, immutably:
- the exact feature snapshot per `Prediction` (model inputs)
- the exact immutable `ModelVersion` on each `Prediction`
- every `DecisionRecord` (per cycle, never overwritten): per-action
  predictions, per-action `{cost_used, eirv_value}` + amount,
  `recommended_action`, `final_action` (stored separately), `decision_reason`
- every `PolicyEvaluation` (per candidate): policy id + version, result,
  reason code
- every `Intervention` executed, with `execution_status`, timestamps,
  channel, cost
- the `Outcome` (`result`, `recovery_amount`, `observed_at`)
- the status-transition history (`recovery_case_status_history`)

Persisted EIRV inputs/outputs mean a historical decision is explainable
and re-derivable **without** today's model, policy, or cost config.

This audit information is generated by the system from these structured
events/records. It is **not** produced or determined by the LLM — the LLM
may later *phrase* an explanation from the stored records, but it is never
the authoritative source of what happened.

Any case can be fully reconstructed and explained after the fact — see
`data/database-schema.md` for the schema that guarantees this. The exact
implementation lands in later phases; this is a documentation requirement
now.

### D. Immutability of the audit record (Phase 1A.3 / ADR-011)

The following are immutable once written — a change is a new record, never
an edit — so the audit trail cannot be silently rewritten:

```
ModelVersion            (except its lifecycle status field)
Policy version
Prediction
DecisionRecord
ExperimentAssignment
PolicyEvaluation (historical)
Intervention (historical, once resolved)
Outcome (once resolved)
PaymentEvent (already: append-only, ADR-009)
```

### E. Experimentation cannot weaken safety (Phase 1A.3 / ADR-011)

A `RecoveryCase`'s experiment arm (`CONTROL` / `TREATMENT`, assigned once
at the case level, immutable) may change *which model/strategy* produces
the recommendation. It can **never**:

- bypass recovery eligibility;
- bypass the Policy Engine or its unconditional veto (ADR-004);
- authorize an action policy would block;
- remove `NO_ACTION` from the candidate set;
- force an intervention because a case is in `TREATMENT`.

Ordering is fixed: `experiment arm → predictions → EIRV → recommendation →
policy evaluation → final action`. The hackathon's primary experiment
mechanism is **offline** over historical/synthetic data; controlled live
experimentation is a future capability bounded by eligibility, policy,
risk limits, small cohorts, and auditability — never unrestricted or
RL-style exploration.

### F. System/credential security

```
- No secrets in code or in LLM prompts (see privacy-architecture.md).
- Razorpay webhook signatures verified on every inbound webhook
  (integrations/webhooks.md) before any event is trusted.
- API keys loaded only from environment variables (.env, never committed —
  see .gitignore and .env.example).
- Idempotency: repeated webhook delivery for the same event must not
  trigger duplicate interventions (Redis-backed idempotency key per event id).
```

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Let the model's confidence score gate action instead of explicit policy rules | Confidence and "is this action *allowed*" are different questions; conflating them would let a sufficiently confident model bypass hard business/legal constraints — explicitly disallowed by the core principle "never bypass policy because the model predicts higher revenue." |
| No explicit stopping rule; rely on diminishing incremental value to naturally taper interventions | Diminishing value is a real and desired effect, but an explicit hard cap is needed as a backstop so a modeling error can never cause unbounded customer contact. |

## 5. Why this option

Hard, explicit, inspectable rules plus an immutable audit trail is the only
design that lets the product credibly claim "bounded and gated" and "no
runaway automated behavior" — a judge or a real merchant should be able to
read the current policy config and know exactly what the system can and
cannot do, without needing to trust the ML model's judgment.

## 6. Example

```
Case RC-9931: 3rd retry attempt on a ₹200 payment, customer already
              contacted twice this week.
Policy check: max_retry_count (2) already reached → RETRY blocked
              max_customer_contacts (2/7 days) already reached → MESSAGE blocked
              → recommended_action was RETRY, final_action = NO_ACTION
              → case moves to STOPPED (stopping rule: limits reached)
              → DecisionRecord logs: recommended RETRY, policy BLOCKED (x2),
                final NO_ACTION, reason "retry and contact limits reached"
```

## 7. Implementation implications

- Policy rules should be data (a `Policy` row per merchant), not hardcoded
  constants, from Phase 5/6 onward — see `data/database-schema.md`.
- Webhook signature verification (`integrations/webhooks.md`) must be
  implemented before any real Razorpay webhook is trusted (Phase 8) — never
  skipped "temporarily" for demo convenience with real credentials.

## 8. Open questions

- Exact default numeric limits (retry count, contact window, time-to-expire)
  are placeholders above and should be tuned once the synthetic data /
  simulator (Phase 2) gives a realistic sense of scale.

## 9. Visual

```
        ACTION OPTIMIZER proposes an action
                    │
                    ▼
        ┌───────────────────────┐
        │     POLICY ENGINE     │◄── Policy config (per merchant)
        │  retry/contact limits │◄── Contact history
        │  consent, amount caps │◄── Risk flags
        └───────────┬───────────┘
                    │
        ┌────────────┴────────────┐
        ▼                         ▼
     ALLOWED                  BLOCKED (+ reason)
        │                         │
        ▼                         ▼
        └────────► DecisionRecord ◄──────── (recommended vs final action)
                        │
        ┌───────────────┴───────────────┐
        ▼                               ▼
   ACTION GATEWAY               next-best action / NO_ACTION
        │
        ▼
   Intervention (only if final_action ∈ {RETRY, MESSAGE};
   NO_ACTION → no Intervention) + immutable audit trail either way
```
