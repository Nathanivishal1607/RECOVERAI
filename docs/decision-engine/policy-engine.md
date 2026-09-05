# Policy Engine

## 1. Purpose

Define the deterministic rule layer that has final veto power over every
action the ML/optimization layer proposes — the concrete mechanism behind
the non-negotiable principle "never bypass policy because the model
predicts higher revenue."

## 2. Context

Implements step 7 of `architecture/decision-flow.md` and the rule set
described in `architecture/security-and-safety.md`. This is deliberately
the simplest, most inspectable code in the entire system — a merchant or a
judge should be able to read the active policy for a merchant and know
exactly what the system can and cannot do to that merchant's customers.

## 3. Current decision

### What the policy layer can express

The policy layer sits between AI/ML decisioning and financial execution and
supports concepts such as:

```
- maximum retries
- maximum customer contacts
- allowed intervention types
- consent requirements
- restricted contact hours
- merchant-specific policies
- amount limits
- risk thresholds
- stopping rules
```

The MVP implements a small real subset of these (retry limit, contact
limit, allowed interventions, consent, amount limit, risk flag); the rest
are documented here as the intended shape and are added as data
(`Policy` fields), not code, when needed.

### `Policy` (data) vs. Policy Engine (logic) — finalized Phase 1A.3 / ADR-011

```
Policy        = WHAT is allowed   (data: rule values, versioned, immutable per version)
Policy Engine = HOW it's evaluated (this document: fixed, deterministic code)
```

A `Policy` **version** is immutable — a policy change creates a **new**
version; historical versions are never edited in place. A `Policy`
belongs to a `Merchant` (`Merchant 1 ── * Policy version`), and the system
must be able to identify exactly which version was evaluated for any
historical decision (already required by `PolicyEvaluation.policy_version`
— Phase 1A.2, unchanged). No arbitrary executable policy code is part of
the MVP design — only structured data interpreted by this fixed engine.
Full entity contract: `data/data-model.md` "Model, Policy & Experiment
data contract".

**The ML system must never bypass policy because it predicts greater
revenue.** Hard, binary checks — never soft penalty terms inside EIRV.

### Rule evaluation (pure function, no ML, no randomness)

```python
def check_policy(case, action: str, policy: Policy, contact_history) -> PolicyResult:
    if action == "NO_ACTION":
        return PolicyResult(allowed=True, reason="no action always allowed")

    if action not in policy.allowed_interventions:
        return PolicyResult(allowed=False, reason=f"{action} not enabled for this merchant")

    if action == "RETRY" and case.attempt_number > policy.max_retry_count:
        return PolicyResult(allowed=False, reason="max retry count exceeded")

    if contact_history.count_in_window(case.customer_id, policy.contact_window_days) \
            >= policy.max_customer_contacts:
        return PolicyResult(allowed=False, reason="max customer contacts exceeded")

    # VOICE is a post-MVP action (see integrations/voice.md); this check is
    # shown for forward-compatibility and is inert until VOICE is enabled.
    if action == "VOICE" and not case.customer.consent_voice:
        return PolicyResult(allowed=False, reason="no voice consent on file")

    if policy.max_autonomous_amount and case.amount_at_risk > policy.max_autonomous_amount:
        return PolicyResult(allowed=False, reason="amount exceeds autonomous action limit — escalate")

    if case.has_risk_flag:
        return PolicyResult(allowed=False, reason="risk flag present — escalate, no autonomous action")

    return PolicyResult(allowed=True, reason="all checks passed")
```

Every check is persisted as a `PolicyEvaluation` record (Phase 1A.2 /
ADR-010) — **one per candidate action the veto loop evaluated**, attached
to the `DecisionRecord` for that cycle:

```
PolicyEvaluation
├── decision_record_id
├── action            (the candidate evaluated)
├── policy_id
├── policy_version    (which version of the merchant policy governed this)
├── result            ALLOWED | BLOCKED
├── reason_code       MAX_RETRY_LIMIT | MAX_CONTACTS | CHANNEL_DISABLED
│                     | NO_CONSENT | AMOUNT_LIMIT | RISK_FLAG | ...
├── reason            (human-readable detail)
└── evaluated_at
```

`PolicyEvaluation` is **not** stored on the `Intervention` row — policy
authorization (the ML/economic recommendation being *allowed*) and
execution (what the provider did) are separate records. Nothing is checked
without being logged, and a `BLOCKED` result on the *recommended* action
is retained even when `final_action` ends up `NO_ACTION` — the
recommendation vs. authorization vs. execution distinctions are never
lost.

### Guaranteed termination

`NO_ACTION` always returns `allowed=True` unconditionally — this is what
guarantees the veto loop in `decision-engine.md` always terminates.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Encode policy as weights/penalties inside the EIRV formula (soft constraints) | Explicitly rejected — see `architecture/security-and-safety.md`: a sufficiently high predicted value must never be able to buy its way past a hard rule like a consent requirement or a contact limit. Hard, binary checks are the only design that can make that guarantee. |
| Let the LLM evaluate policy compliance | The LLM has no role in financial decisioning per `architecture/decision-flow.md`; policy must be deterministic and reproducible for the audit trail, which rules out LLM judgment here entirely. |
| Global (not per-merchant) policy | Rejected — merchants have different risk tolerances and channel availability (per `product/users.md`); a single global policy couldn't express "Merchant A has voice disabled." |

## 5. Why this option

A short, ordered list of independent boolean checks, each with a specific
human-readable reason, is the simplest design that's still fully expressive
of every rule identified in `architecture/security-and-safety.md`, and it's
trivially testable — each rule can be unit tested in isolation.

## 6. Example

See `architecture/security-and-safety.md` section 6 for a full worked
example of a blocked case.

## 7. Implementation implications

- `backend/policies/engine.py` (Phase 5/6) implements this function; per
  the dependency rule in `architecture/component-architecture.md`, this
  module must have zero imports from `backend/decision_engine` or `ml/`.
- Policy configuration is data (`Policy` table row), not code — adding a
  new merchant-specific limit should never require a code change, only a
  new row/field value.

## 8. Open questions

- Whether `has_risk_flag` (referenced above) is populated by a separate,
  simple deterministic risk-flagging step (e.g. amount anomaly, too many
  recent failures) for MVP, or deferred entirely as a Track-2-style concern
  out of scope for this product — current lean: a minimal placeholder flag
  for MVP, not a full risk model, to keep Track boundaries clean per
  `product/problem-statement.md`.

## 9. Visual

See the veto-loop diagram in `architecture/security-and-safety.md` section 9 —
this document specifies the `POLICY ENGINE` box in that diagram in full detail.
