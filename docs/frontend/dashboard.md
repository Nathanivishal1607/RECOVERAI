# Dashboard

## 1. Purpose

Specify the judge-facing and merchant-facing dashboard, designed around the
principle that no decision in this system should be a black box.

## 2. Context

Product planning explicitly designed the "killer demo" around a dashboard
that shows the baseline-vs-RecoverAI comparison, individual case
reasoning, and the model's improvement over time — this document is the
implementation-ready version of that plan.

## 3. Current decision

### Screen 1 — Command Center (landing view)

```
┌─────────────────────────────────────────────┐
│              RECOVERAI                      │
│        Revenue Recovery Command Center       │
├─────────────────────────────────────────────┤
│  [Synthetic data] banner (always visible     │
│   while running on simulated data)           │
│                                               │
│  Revenue at Risk      Revenue Recovered      │
│  ₹18.4L                ₹11.7L                │
│                                               │
│  Recovery Rate         Incremental Revenue   │
│  63.5%                 +₹3.4L  (vs baseline) │
│                                               │
├─────────────────────────────────────────────┤
│  Recent Recovery Cases                       │
│  ₹5,000  UPI Failed   MESSAGE   Recovered    │
│  ₹8,500  Card Failed  RETRY     Recovered    │
│  ₹2,100  Timeout      NO_ACTION Unrecovered  │
└─────────────────────────────────────────────┘
```

### Screen 2 — Case detail (click-through from any row)

Must show, for every case, without exception:

```
- Amount, payment method, failure category
- Baseline probability + per-action probabilities (with ModelVersion)
- EIRV per candidate action
- DecisionRecord: recommended_action + final_action shown distinctly
  (and why they differ, if they do)
- Every policy check performed, allowed/blocked + reason
- Executed action + execution result (Intervention), if any
- Outcome (if known)
- Case state (state machine) and, for terminal STOPPED, the stopping reason
- If NO_ACTION was the final action: distinguish "best economic choice" vs.
  "recommendation was blocked by policy" (see decision-engine/action-selection.md)
```

### Screen 3 — Baseline vs. RecoverAI comparison (UC-3)

```
Run the same batch through the fixed baseline strategy and RecoverAI,
side by side (exact table format specified in ml/evaluation.md section 6).
```

### Screen 4 — Model learning view (UC-6)

```
"Model v2 trained on 1,200 new cases — improved AUC 0.81 → 0.85,
 calibration ECE 0.04 → 0.03 — promoted on [date]"
(pulled directly from ml/learning-loop.md's model metadata)
```

## 4. Alternatives considered

Considered a minimal dashboard showing only the headline numbers (Screen 1
only), to save build time. Rejected — the case-detail explainability view
(Screen 2) is not a nice-to-have; it's the concrete proof of the
architecture's core safety/auditability claims, and is one of the cheapest
screens to build (it's a direct read of already-persisted data — see
`data/database-schema.md`).

## 5. Why this option

Every screen maps directly to an existing use case (`product/use-cases.md`)
and an existing data structure already specified elsewhere — nothing here
requires new backend concepts, only presentation of what the architecture
already produces and persists.

## 6. Example

See the ASCII mockups in section 3 above.

## 7. Implementation implications

- Frontend (Next.js/TypeScript/Tailwind/shadcn-ui, Phase 11) should treat
  Screen 2 (case detail) as a direct rendering of the
  `RecoveryCase` + `ModelPrediction` + `DecisionRecord` + `Intervention` +
  `Outcome` API response — no separate "explanation" data structure needs
  to be invented.
- The "[Synthetic data]" banner (section 3) is a product requirement per
  `data/synthetic-data.md`, not optional styling.

## 8. Open questions

- Exact chart library/visual treatment — deferred to Phase 11
  implementation; functionally, bar/line charts for the comparison and
  learning-curve views are sufficient, no need to over-invest in custom
  visualization for a hackathon dashboard.

## 9. Visual

See mockups in section 3.
