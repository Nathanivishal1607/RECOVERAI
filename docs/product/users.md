# Users

## 1. Purpose

Identify who RecoverAI is built for, so every design/engineering decision
has a concrete person to be judged against.

## 2. Context

RecoverAI is a B2B(2C) system: the direct user is the merchant (or the
merchant's finance/growth team), but its decisions directly affect the
merchant's customers, who receive interventions (retry prompts, messages,
calls).

## 3. Current decision — primary and secondary users

### Primary user: the Merchant Operator

Who they are: a small-to-mid-size e-commerce or subscription business owner,
or someone on their finance/growth team, using Razorpay for payments.

What they want:
- Fewer failed payments turning into permanently lost revenue.
- Confidence that automated recovery attempts won't annoy or alienate
  customers.
- Visibility into *why* the system did what it did (explainability), because
  money and customer relationships are both at stake.
- Configurable limits (max retries, max contacts, allowed channels) — they
  do not want to hand over unbounded authority to an AI.

### Secondary user: the End Customer

Who they are: someone whose payment failed while trying to buy something or
pay a subscription/bill.

What they experience: at most a small number of well-timed, relevant
recovery touches (a retry, a `MESSAGE` — delivered via WhatsApp/SMS/Email
post-MVP — and optionally a voice call in their language once the `VOICE`
extension exists) — never spam, never repeated contact past the merchant's
configured limit.

### Tertiary "user": the Buildathon Evaluator

Not a real end user, but a design constraint worth naming explicitly: this
person needs to be able to look at the system and answer "can this person
actually build AI systems?" within a 5-minute video and a GitHub repo. Every
documentation and UX decision should keep this reader in mind alongside the
merchant.

## 4. Alternatives considered

| Alternative framing | Why not chosen |
|---|---|
| Treat the end customer as the primary user (consumer-facing recovery app) | The actual buyer/adopter of this kind of system is the merchant; customer experience is a constraint on the product, not the product's target user. |
| Treat Razorpay itself as the "user" (i.e. build an internal Razorpay tool) | Out of scope for a Buildathon submission — we're a hackathon team building a standalone demonstrable product, not modifying Razorpay's internals. |

## 5. Why this option

Framing the merchant as primary user, with hard-coded respect for the end
customer's experience via the policy engine, matches how the architecture
treats "customer friction" — as a **hard policy constraint** (max
contacts, consent, restricted hours), not a soft penalty traded off
against revenue inside EIRV (see `decision-engine/policy-engine.md` and
`ml/uplift-modelling.md`).

## 6. Example — a day in the life

```
Merchant "ABC SaaS" logs into RecoverAI dashboard:
  - sees 42 failed payments overnight
  - RecoverAI already triaged: 18 NO_ACTION (would recover anyway or
    not worth intervening), 15 RETRY, 9 MESSAGE with a payment link
    (simulated message gateway for the MVP)
  - by morning: 21 of the 24 interventions recovered ≈ ₹1.4L
  - merchant clicks into one case to see exactly why a second contact was
    NOT made for a customer (contact limit already reached) — full audit
    trail visible
  - (post-MVP, the same view explains why a VOICE call was not chosen —
    e.g. no consent on file)
```

## 7. Implementation implications

- The dashboard (`frontend/dashboard.md`) must expose *why*, not just *what*,
  for every decision — this is a user need, not a nice-to-have.
- Merchant-configurable policy (`decision-engine/policy-engine.md`) is a
  first-class feature, not an afterthought bolted on for safety theater.

## 8. Open questions

- Whether the hackathon demo will simulate a single merchant persona or
  multiple merchant personas with different policy configs (leaning toward
  2–3 contrasting merchant profiles to show policy actually changes
  behavior — to be finalized in Phase 2, synthetic data design).

## 9. Visual

```
        RAZORPAY / PAYMENT EVENTS
                   │
                   ▼
              RECOVERAI  ───────────────► MERCHANT OPERATOR
                   │                       (dashboard, policy config,
                   │ intervention           audit trail)
                   ▼
            END CUSTOMER
     (retry prompt / message / call —
      bounded by merchant's policy)
```
