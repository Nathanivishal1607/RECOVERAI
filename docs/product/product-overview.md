# Product Overview

## 1. Purpose

Define what RecoverAI is, in one place, at a level any new contributor
(engineer, judge, teammate) can read in under five minutes.

## 2. Context

This project targets the Razorpay AI Buildathon, Track 3 — **AI Revenue
Recovery**. Razorpay's stated bar for this track: detect revenue at risk,
determine the right intervention, execute a bounded recovery workflow, and
demonstrate **measured money recovered across a batch**, with compliant
escalation, stopping rules, and an audit trail.

Three tracks were seriously evaluated before this one was chosen — AI Growth
& Agentic Commerce, AI Risk Manager, and AI Revenue Recovery. The full
comparison and why Revenue Recovery won is recorded in
`docs/decisions/architecture-decisions.md` (ADR-000).

## 3. Current decision

**Product name:** RecoverAI
**Category:** AI Revenue Recovery Decision Engine
**Track:** Razorpay Buildathon — Track 3 (AI Revenue Recovery)

**One-line pitch:**

> When a payment fails, RecoverAI decides whether intervention is worth it,
> which intervention maximizes incremental recovered revenue, and when to
> stop — under merchant policy, privacy, and financial-safety constraints.

**What makes this different from "smart retry":** Stripe (Smart Retries),
Chargebee (Smart Dunning), and Razorpay itself already ship ML-driven retry
timing. Building another retry-timing product would compete directly with
tools our target evaluators already know. RecoverAI's differentiation is
that it treats "retry" as just one of several possible actions, and its
central question is not "will this recover?" but "would this have recovered
**anyway**, and is intervening actually worth its cost?" — i.e. it is an
**incremental value decision engine**, not a retry scheduler.

## 4. Alternatives considered

| Alternative | Why not chosen as primary |
|---|---|
| AI Commerce Gateway for SMBs (Track 1) | Shopify Catalog, OpenAI ACP/product feeds, and Stripe's Agentic Commerce Suite already solve most of "make a merchant AI-readable." Viable only if reframed as an *AI-commerce competitiveness* product, which is a bigger research bet. Kept as a documented backup idea, not built. |
| AI Fraud Ring Investigator (Track 2) | Strong idea, but requires a graph-modelling investment and a fraud-labelled dataset that's harder to simulate convincingly than payment recovery. Kept as backup. |
| Generic "AI payment retry" (Track 3, naive version) | Too close to Razorpay's own Subscription Recovery Agent / Intelligent Retry Engine, and to Stripe/Chargebee's existing products. Rejected as the primary framing; retry becomes one action inside a larger decision engine instead. |

## 5. Why this option

- Directly matches the published Track 3 bar (detect → intervene → measure → audit → stop).
- Produces a single, unambiguous headline metric a judge can hold onto: **incremental revenue recovered**, not "accuracy."
- Buildable end-to-end with synthetic data if real Razorpay data isn't available (see `data/synthetic-data.md`).
- Fits the team's existing strengths: ML/data pipelines, FastAPI, APIs, and Sarvam/Indian-language speech-to-text (usable later as an optional voice-recovery channel, not the core product).
- Naturally supports the privacy-by-design and "AI must not directly control money" principles that were treated as non-negotiable from the start of product discovery.

## 6. Example

```
Payment failure event
  amount: ₹5,000, method: UPI, failure: timeout, attempt: 1
        │
        ▼
RecoverAI predicts:
  P(recover | NO_ACTION)   = 28%
  P(recover | RETRY)       = 51%
  P(recover | MESSAGE)     = 67%
        │
        ▼
Incremental value (illustrative simulated costs):
  RETRY    → (0.51-0.28) × ₹5,000 - cost  ≈ ₹1,130
  MESSAGE  → (0.67-0.28) × ₹5,000 - cost  ≈ ₹1,930   ← highest
        │
        ▼
Policy engine: MESSAGE allowed for this merchant? contact limit ok? → YES
        │
        ▼
Action executed → outcome recorded → case closed → feeds next training batch
```

(The three MVP actions are `RETRY`, `MESSAGE`, and `NO_ACTION`. `MESSAGE`
is an abstract intervention; concrete delivery channels — WhatsApp / SMS /
Email, and Voice — are post-MVP extensions behind a message gateway. See
`product/mvp-scope.md` and `integrations/messaging.md`.)

## 7. Implementation implications

- Every architectural document in this repo must preserve the distinction
  between "recovery probability" and "incremental recovery probability."
  Anything that blurs this (e.g. a dashboard showing raw recovery rate as
  the headline number) is a spec violation, not a style choice.
- Documents must likewise keep **Prediction** ("what is likely"),
  **Recommendation** ("what we should do"), and **Execution** ("what we
  actually did") as separate things — the recommended action and the
  executed action can differ, and the `DecisionRecord` records both (see
  `data/data-model.md`).
- The product's credibility rests on the evaluation methodology (baseline vs
  RecoverAI, on the same synthetic batch) being rigorous and clearly
  presented — this is as important as the code.

## 8. Open questions

- Whether real, anonymized Razorpay sandbox/test-mode data will be available
  before the demo, or whether the entire evaluation will run on synthetic
  data only (current assumption: synthetic, see `data/synthetic-data.md`).
- Whether Track 3 submission requirements will require a live Razorpay
  test-mode transaction flow or whether a convincingly simulated flow is
  acceptable — to be confirmed against the official Buildathon page closer
  to submission.

## 9. Visual — where this sits relative to the whole system

```
        PRODUCT LAYER (this doc)
                │
                ▼
        ARCHITECTURE  →  see architecture/system-architecture.md
                │
                ▼
        DATA + ML     →  see data/*.md, ml/*.md
                │
                ▼
        DECISION ENGINE → see decision-engine/*.md
                │
                ▼
        INTEGRATIONS + UI → see integrations/*.md, frontend/*.md
```
