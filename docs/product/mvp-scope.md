# MVP Scope

## 1. Purpose

Draw a hard line around what "v1 / MVP" means so implementation doesn't
silently expand into every idea discussed during product exploration.

## 2. Context

The planning discussion generated a large number of good ideas (voice
recovery, WhatsApp, checkout abandonment, subscription recovery, B2B
receivables, multi-merchant policy configs, contextual bandits, MLflow,
etc.). Left unchecked, a hackathon team building all of it ends up finishing
none of it well. This document is the enforcement mechanism against that
failure mode.

## 3. Current decision

### In scope for MVP

- **Trigger:** a `PAYMENT_FAILED` payment-lifecycle event only (internal
  `PaymentEvent` vocabulary — see `data/events.md`). A failed payment does
  **not** automatically create a `RecoveryCase`: it passes a **recovery
  eligibility** gate first (see `data/data-model.md`).
- **Payment status:** a lean internal vocabulary (`CREATED`, `PROCESSING`,
  `FAILED`, `SUCCEEDED`, `CANCELLED`) with provider states mapped in — not
  provider states passed through raw.
- **Actions (exactly these three):** `RETRY`, `MESSAGE`, `NO_ACTION`.
  - `MESSAGE` is an **abstract, provider-agnostic intervention**. The
    decision engine only ever chooses `MESSAGE`; a **message gateway**
    (simulated for the MVP) is responsible for concrete delivery. This
    keeps future channels addable without changing the decision engine:

    ```
    Decision Engine
          ↓
        MESSAGE
          ↓
    Message Gateway
          ↓
    WhatsApp / SMS / Email        (post-MVP; simulated in the MVP)
    ```
  - `WHATSAPP`, `SMS`, `EMAIL` are **future channel implementations**
    behind that gateway — not separate MVP actions.
  - `VOICE` is a **future extension** (a distinct action, not a `MESSAGE`
    channel) and must **not** be required for the MVP or block the core
    engine.
- **Models:** baseline (no-intervention) recovery probability +
  per-action recovery probability, via LightGBM/logistic regression on
  synthetic data (Phase 3), followed by a simple uplift/incremental
  estimate (Phase 4).
- **Decision engine:** expected incremental value calculation + action
  optimizer + policy engine with a small, real rule set (max retries, max
  contacts, amount limits) — Phase 5.
- **Backend:** FastAPI + PostgreSQL + Redis, Recovery Case lifecycle,
  synthetic-event-driven (real Razorpay webhooks added in Phase 8, not
  before).
- **Learning loop:** outcome recording from day one; batch retrain +
  validate + promote, demonstrable even if only run manually/on-demand for
  the hackathon (Phase 7).
- **LLM:** explanation of decisions + draft customer message text, behind
  the privacy filter (Phase 9). Not required for the system to function —
  it must degrade gracefully if disabled.
- **Dashboard:** revenue at risk, revenue recovered, incremental revenue,
  case list/detail with full reasoning, baseline-vs-RecoverAI comparison
  (Phase 11).
- **Synthetic data:** the primary evaluation dataset, with documented
  hidden ground truth (Phase 2).

### Explicitly out of scope for MVP (documented, not forgotten)

| Idea | Status |
|---|---|
| Real WhatsApp / SMS / Email sending (concrete `MESSAGE` channels behind the message gateway) | Extension — Phase 10, optional |
| Real voice recovery (Sarvam, Hinglish) — a distinct `VOICE` action | Extension — Phase 10, optional, must not block core engine |
| Checkout abandonment recovery | Future direction — different event type |
| Subscription/mandate retry sequencing | Future direction |
| B2B receivables / promise-to-pay tracking | Future direction |
| Contextual bandit / online exploration | Future direction — noted in ml/learning-loop.md as a v2 idea |
| MLflow / formal model registry | Only if plain versioned artifacts become unmanageable |
| Multiple LLM providers, multi-agent orchestration | Rejected outright, see docs/decisions |
| Kafka/Kubernetes/Spark/Airflow/Prometheus/Grafana | Rejected outright for hackathon scope |
| AI Commerce Gateway (Track 1 idea) | Backup idea only, not built |
| AI Fraud Ring Investigator (Track 2 idea) | Backup idea only, not built |

## 4. Alternatives considered

Considered scoping the MVP around 2 event types (`payment.failed` +
`checkout.abandoned`) to make the demo feel richer. Rejected: doubling the
event/feature surface before the core decision engine is proven doubles
the risk of finishing neither well, for a marginal demo improvement that
voice/WhatsApp extensions deliver more cheaply anyway.

## 5. Why this scope

This is the smallest scope that still fully exercises every architectural
principle the product depends on: incremental (not raw) value, LLM-out-of-
the-loop-for-money, policy enforcement, do-nothing as a real decision, and a
learning loop. Anything cut from this list is cut precisely because it adds
surface area without adding proof of a new principle.

## 6. Example — MVP demo script (see also decision-engine docs for the mechanics)

```
1. Load N synthetic failed payments (Phase 2 dataset)
2. Run fixed baseline strategy → recovered revenue R_baseline
3. Run RecoverAI (predict → incremental value → optimize → policy → act)
   → recovered revenue R_recoverai
4. Show: incremental revenue = R_recoverai - R_baseline, with fewer
   total interventions than baseline
5. Drill into 2-3 individual cases showing full reasoning + policy checks
6. Show model v1 → v2 improvement after feeding new outcomes back in
```

## 7. Implementation implications

- No implementation task should introduce a new event type, action type, or
  external provider integration without first updating this document.
- Every phase's "done" criteria should be checked against this scope before
  being reported complete.

## 8. Open questions

- Whether `MESSAGE` in the MVP needs to hit a real (even if free-tier)
  WhatsApp/SMS sandbox to be credible for judges, or whether a clearly
  logged/simulated send is acceptable until Phase 10. Current assumption:
  simulated is acceptable for MVP; real integration is Phase 10's job.

## 9. Visual

```
                         MVP BOUNDARY
        ┌───────────────────────────────────────────┐
        │  PAYMENT_FAILED → eligibility → RecoveryCase│
        │  → predict → incremental value → optimize  │
        │  → policy → DecisionRecord → {retry,       │
        │  message(simulated), none} → outcome →     │
        │  learning loop → dashboard                 │
        └───────────────────────────────────────────┘
                 │                    │
                 ▼                    ▼
        Phase 10 extensions    Future directions
        (real WhatsApp/voice)  (checkout abandonment,
                                subscriptions, B2B, ...)
```
