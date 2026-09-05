# Problem Statement

## 1. Purpose

State precisely what problem RecoverAI solves, and — just as importantly —
what it deliberately does not try to solve in v1.

## 2. Context

Merchants lose revenue continuously through failed payments. Not all of that
revenue is equally recoverable, and not every failure deserves the same
response. Today, most systems treat this as a scheduling problem ("when do
we retry?"). Razorpay's own Track 3 brief explicitly asks for something
larger: detection of revenue at risk, diagnosis, a bounded recovery action,
and **measured** money recovered — not just an intelligent retry timer.

## 3. Current decision — the problem, precisely

> When a payment fails, determine **whether intervention is worthwhile at
> all**, **which intervention** (`RETRY` / `MESSAGE` / `NO_ACTION` for the
> MVP) is most likely to create **incremental** recovery — recovery that
> would not have happened anyway — and **when to stop**, while respecting
> merchant policies, customer contact limits, privacy constraints, and
> financial safety.

(The MVP intervention set is exactly `RETRY`, `MESSAGE`, `NO_ACTION`.
`MESSAGE` is an abstract intervention; concrete channels — WhatsApp, SMS,
Email — and a `VOICE` action are post-MVP extensions. See
`product/mvp-scope.md`.)

Three sub-problems, each real and distinct (preceded by a gate):

0. **Eligibility** — should this failed payment enter the recovery system
   at all? A failed payment does not automatically become a recovery case;
   it first passes an eligibility check (supported/active merchant,
   recoverable amount, within the recovery window, no active case already,
   policy permits). This is a gate, separate from the incremental-value
   question below. See `data/data-model.md`.
1. **Diagnosis** — why did this payment fail? (timeout, insufficient funds,
   authentication failure, risk block, customer abandonment...)
2. **Recoverability estimation** — under `NO_ACTION`, and under each
   candidate action (`RETRY`, `MESSAGE`), what is the probability of
   recovery? (see `ml/models.md`)
3. **Decisioning** — given those probabilities, costs, and policy
   constraints, what is the single best allowed action, if any? (see
   `decision-engine/decision-engine.md`)

## 4. Alternatives considered (framing of the problem itself)

| Framing | Why not chosen |
|---|---|
| "Maximize recovery rate" | Rewards intervening on everyone, including customers who would have paid anyway — inflates apparent impact without creating real incremental value. |
| "Minimize time-to-recovery" | A reasonable secondary metric, but optimizing for speed alone can encourage excessive/aggressive contact, which conflicts with the customer-friction and policy principles. |
| "Retry-timing optimization" (Stripe/Chargebee-style) | Already well-solved by incumbents and by Razorpay's own tooling; not a differentiated hackathon story. Retry becomes one action among several instead of the whole product. |

## 5. Why this framing was chosen

- It matches Razorpay's own published Track 3 bar almost word for word.
- It produces one clean, defensible headline metric: incremental revenue
  recovered, measured against a baseline strategy on the same data.
- It forces the kind of engineering rigor (counterfactual reasoning,
  calibrated probabilities, policy constraints, audit trails) that the
  Buildathon brief explicitly rewards across every track ("don't hide
  failures," "explainable, bounded, gated" actions).

## 6. Example — real vs. inflated recovery

```
Customer X: payment fails, but customer independently retries and pays
            10 minutes later, with no intervention from us.

Naive system:  "We recovered ₹5,000!" (WRONG — nothing we did caused this)
RecoverAI:     baseline model already predicted 85% natural recovery
               probability → correctly assigns near-zero credit to any
               intervention here → likely decision: DO NOTHING
```

```
Customer Y: payment fails, natural recovery probability is 20%.
            We send a MESSAGE with a payment link (channel: simulated for
            MVP; WhatsApp/SMS/Email post-MVP); customer pays within the hour.

RecoverAI:  P(recover | NO_ACTION) = 20%, P(recover | MESSAGE) = 65%
            → incremental value ≈ (0.65 - 0.20) × amount - cost
            → this IS a defensible recovery attributable to the system
```

## 7. Implementation implications

- Every model, metric, and dashboard number must be traceable to this
  distinction. "Recovered ₹X" without a baseline comparison is not an
  acceptable way to report results anywhere in this project — internally,
  in docs, or in the pitch.
- The synthetic data generator must encode a *hidden* natural-recovery
  probability per case so incremental value can actually be evaluated
  against ground truth (see `data/synthetic-data.md`).

## 8. What is explicitly out of scope for v1

- Checkout abandonment recovery (real problem, but a different event type — planned as an MVP extension, not core).
- Subscription/mandate retry sequencing (same — extension, not core).
- B2B receivables / invoice promise-to-pay tracking (different data shape entirely — future direction only).
- Fraud/chargeback handling (belongs to Track 2, not this product).
- Any autonomous action that moves money without passing the policy engine (never in scope, at any phase).

## 9. Open questions

- What counts as an acceptable "cost" unit for an intervention when no real
  messaging/voice provider is wired up yet (placeholder cost model needed
  until Phase 10) — see `ml/uplift-modelling.md` open questions.

## 10. Visual — problem decomposition

```
              PAYMENT_FAILED  ──►  RECOVERY ELIGIBILITY  ──► (ineligible: no case)
                                          │ eligible
                                          ▼
                    REVENUE AT RISK (RecoveryCase opened)
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
         DIAGNOSIS     RECOVERABILITY      DECISIONING
      "why did it      ESTIMATION          "given the above,
       fail?"          "what's the         what's the single
                        recovery odds       best allowed
                        per action?"        action, if any?"
```
