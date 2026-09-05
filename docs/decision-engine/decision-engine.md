# Decision Engine

## 1. Purpose

Describe the decision engine as a whole — how `value-calculation.md`,
`action-selection.md`, and `policy-engine.md` fit together into the single
component responsible for turning predictions into one final, allowed
action.

## 2. Context

This is `backend/decision_engine/` from `architecture/component-architecture.md`,
and implements steps 5-9 of `architecture/decision-flow.md` (incremental
value → recommendation → policy → DecisionRecord → hand-off to execution).
It is the
component most directly responsible for the product's central technical and
safety claims.

## 3. Current decision

### Responsibilities (and non-responsibilities)

```
DOES:
  - call ml/inference to get P(recover | features, action) per candidate action  [Prediction]
  - compute EIRV per action (value-calculation.md)
  - rank actions; the top-EIRV action is the recommendation (action-selection.md)  [Recommendation]
  - invoke the policy engine to validate/veto (policy-engine.md)
  - loop to the next-best action if the top choice is blocked
  - always terminate in a decision (including, ultimately, NO_ACTION)
  - emit a DecisionRecord for every evaluate→decide cycle, executed or not,
    recording recommended_action AND final_action separately  [Decision]

DOES NOT:
  - call Razorpay or any external provider directly (that's `integrations/`,
    invoked only after a decision is finalized)
  - call the LLM (the LLM is a side channel — see architecture/decision-flow.md)
  - decide policy rules itself (policy is merchant-configured data, not
    decision-engine logic — see policy-engine.md)
  - decide recovery ELIGIBILITY (whether a payment enters the recovery
    system at all) — that gate runs upstream, before a RecoveryCase
    exists; the decision engine only runs for cases that already exist
```

### Eligibility vs. EIRV (two different questions, two different stages)

| Stage | Runs when | Asks | Output |
|---|---|---|---|
| **Recovery eligibility** | on a `PAYMENT_FAILED` event, before any case | "Should this payment enter the recovery system?" | open a `RecoveryCase`, or not |
| **EIRV / this engine** | for a case in `ANALYZING` | "Which action has the greatest expected incremental value, and is it allowed?" | a `DecisionRecord` |

Do not put EIRV math into eligibility, and do not put eligibility rules
into EIRV. Eligibility is a deterministic gate (supported/ACTIVE merchant,
recoverable amount, recovery window, no active case already, policy
permits recovery). See `data/data-model.md` "Recovery eligibility". Its
full rule set is finalised in this decision-engine phase.

### Experimentation never changes this ordering (Phase 1A.3 / ADR-011)

A `RecoveryCase`'s `ExperimentAssignment` (`CONTROL`/`TREATMENT`, assigned
once at the case level — see `data/data-model.md`) may change *which*
`ModelVersion` or decision strategy produces the `Prediction`s and
`Recommendation` for this case. It changes **nothing else** in the
ordering:

```
Experiment arm (which model/strategy) → Prediction → EIRV → Recommendation
        → Policy Evaluation (ADR-004 unconditional veto, unchanged) → Final Action
```

An experiment can never authorize an action policy would block, never
skips recovery eligibility, and never removes `NO_ACTION` from the
candidate set in either arm.

### Internal flow

```
predict_all_actions(case) -> {NO_ACTION: p0, RETRY: p1, MESSAGE: p2}   [Prediction]
        │
        ▼
compute_eirv(case, predictions) -> {RETRY: v1, MESSAGE: v2}   (NO_ACTION's
                                     EIRV is always 0 by definition)
        │
        ▼
ranked = sort candidates by value, descending
recommended_action = ranked[0]                               [Recommendation]
        │
        ▼
for candidate in ranked:
    if policy_engine.check(case, candidate) == ALLOWED:
        final_action = candidate; break
    else:
        log block, continue
else:
    final_action = NO_ACTION   # guaranteed to pass policy — see policy-engine.md
        │
        ▼
emit DecisionRecord(...)                                     [Decision]
return final_action → Action Gateway                          [Execution]
```

### Output: the `DecisionRecord`

The decision engine's durable output is not "an action string" — it is a
`DecisionRecord` (finalized contract in `data/data-model.md` "Decision data
contract — Phase 1A.2") for **one** evaluate→decide cycle. It is
**structured** (links records; not one JSON blob):

```
cycle_number, decision_timestamp
payment_amount_at_decision
Prediction(RETRY / MESSAGE / NO_ACTION)  — one per candidate action,
    each with recovery_probability + the EXACT model_version_id
value context per candidate: { cost_used, eirv_value }
    (persisted so "why did RETRY win?" is answerable + re-derivable later
     WITHOUT today's model/policy/config — EIRV formula fixed, ADR-003)
recommended_action        (highest-EIRV, PRE-policy)
PolicyEvaluation(...)      — one per candidate the veto loop checked:
    { action, policy_id, policy_version, result, reason_code, reason, evaluated_at }
final_action              (the authorized decision)
decision_reason           (why recommended won; why final differs if it does)
policy_version_ref
```

`recommended_action` and `final_action` are stored **separately** and may
differ (recommended `RETRY`, policy `BLOCKED`, final `NO_ACTION`). Never
collapse them.

Downstream, and **not** part of the `DecisionRecord`:
- `Intervention` — only if `final_action ∈ {RETRY, MESSAGE}`
  (`NO_ACTION` ⇒ no `Intervention`). Carries `execution_status`
  (`REQUESTED` / `ACCEPTED` / `REJECTED` / `FAILED` — no `SUCCEEDED`):
  "the system decided to execute RETRY" ≠ "the provider executed RETRY".
- `Outcome` — the observed payment result for this cycle
  (`RECOVERED` / `NOT_RECOVERED`, `observed_at` for delayed outcomes).
  `execution_status` ≠ `Outcome` ≠ `RecoveryCase.status`.

Historical `DecisionRecord`s are **immutable**; re-evaluation creates a new
one with a higher `cycle_number` — never a mutation of the prior cycle.

### Stopping rules

Before (and as part of) selecting an action, the engine checks whether the
case has reached a **stopping condition**. When one applies, `final_action`
is `NO_ACTION`. Two shades of this (see the `RecoveryCase` state machine in
`data/data-model.md`):

- **No worthwhile/allowed action right now** (no positive EIRV, or policy
  blocked everything) → `NO_ACTION` decision, but the case still goes to
  `WAITING_FOR_OUTCOME` to observe any natural recovery, and may
  re-evaluate later.
- **Hard stop, case is done** (retry/contact limits reached, opt-out,
  recovery already completed) → `NO_ACTION` decision and the case moves to
  terminal `STOPPED` (no re-evaluation).

The conceptual order:

```
Is intervention worthwhile?     (positive expected incremental value?)
        ↓
Is intervention allowed?         (policy engine)
        ↓
Has the case reached a stopping condition?
        ↓
Execute OR stop
```

Stopping conditions (documentation only at this phase — not yet
implemented; see `architecture/security-and-safety.md` section B for where
the hard caps live):

```
Maximum retry attempts reached
Maximum contact attempts reached
No positive expected incremental value for any allowed action
Customer has opted out
Policy restriction (no allowed action remains)
Recovery already completed
Case expired (time window elapsed)
```

## 4. Alternatives considered

Considered merging the decision engine and policy engine into a single
module for simplicity. Rejected — keeping them separate packages
(`backend/decision_engine/` vs `backend/policies/`) with a strict
one-directional dependency makes the "policy can always veto, and the model
can never bypass it" guarantee verifiable by inspecting imports, matching
`architecture/component-architecture.md`'s dependency rule.

## 5. Why this option

A single orchestrating function with a clear, small responsibility list
(and an equally clear non-responsibility list) is the easiest version of
this component to review, test, and explain in the pitch — "here is the
one function that decides what happens to a customer's money, and here is
everything it's explicitly not allowed to do."

## 6. Example

See the full worked trace in `architecture/decision-flow.md` section 6.

## 7. Implementation implications

- `backend/decision_engine/orchestrator.py` (Phase 5) should be a single,
  small, well-tested function implementing exactly the flow in section 3 —
  resist the urge to add branching special cases here; special cases belong
  in policy configuration (data), not in this code path.
- Unit tests (Phase 5/6) should include a case where every non-NO_ACTION
  candidate is blocked, asserting the loop correctly terminates at
  NO_ACTION rather than erroring.

## 8. Open questions

None outstanding beyond those already listed in `value-calculation.md`,
`action-selection.md`, and `policy-engine.md`.

## 9. Visual

```
   ml/inference ──► predictions ──► value-calculation.md (EIRV)
   (+ ModelVersion)                        │
                                          ▼
                                  action-selection.md (rank)
                                          │
                                          ▼
                                  recommended_action
                                          │
                                          ▼
                                  policy-engine.md (veto loop)
                                          │
                                          ▼
                          DecisionRecord {recommended, policy_result, final}
                                          │
                                          ▼
                                   FINAL ACTION ──► Action Gateway ──► Intervention
                                                     (only if FINAL ACTION ∈ {RETRY, MESSAGE};
                                                      NO_ACTION produces no Intervention)
```
