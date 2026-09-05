# ML Overview

## 1. Purpose

Give a one-page map of every ML/statistical component in RecoverAI, and how
they relate — before diving into the detailed docs for features, models,
calibration, uplift, evaluation, and the learning loop.

## 2. Context

Product discussion identified four distinct "AI/ML jobs" that must not be
collapsed into one undifferentiated "AI does it" black box:

```
ML #1 — Risk/recovery prediction:      "Will this payment recover?"
ML #2 — Intervention-specific/uplift:  "Will THIS action increase recovery?"
Optimization #3 — Decisioning:         "Which action has highest expected
                                         incremental value, allowed by policy?"
LLM #4 — Investigation/explanation:    "Why is this happening, in plain language?"
```

This document is the index tying those four jobs to concrete docs and code
locations.

These jobs sit on different sides of the **Prediction → Recommendation →
Execution** split (see `data/data-model.md`):

```
ML #1 / #2   → Prediction     ("what is likely to happen")
Optimization #3 → Recommendation ("what should we do") → DecisionRecord
                → (policy engine) → Execution ("what we actually did")
LLM #4        → explanation only, never any of the above
```

Every `Prediction` is stamped with the exact, immutable `ModelVersion`
that produced it; a `DecisionRecord`'s model version is derived from its
`Prediction`s rather than stored independently — so any decision is still
traceable to an exact model (see `ml/learning-loop.md`,
`data/data-model.md`).

## 3. Current decision — the map

| Job | Doc | Code location | Technology |
|---|---|---|---|
| #1 Baseline recovery prediction | `ml/models.md`, `ml/features.md`, `ml/labels.md` | `ml/models/`, `ml/inference/` | scikit-learn (logistic regression baseline) → LightGBM |
| #1b Per-action recovery prediction | `ml/models.md` | same | Same models, action included as a feature, or one model per action (decided in `ml/models.md`) |
| Calibration | `ml/probability-calibration.md` | `ml/evaluation/` | scikit-learn calibration tools |
| #2 Incremental/uplift estimation | `ml/uplift-modelling.md` | `ml/models/`, `ml/evaluation/` | Difference-in-probabilities baseline → uplift-specific methods (EconML or a documented simpler approach) |
| #3 Decisioning (optimizer + policy) | `decision-engine/*.md` | `backend/decision_engine/`, `backend/policies/` | Deterministic Python |
| #4 LLM investigation/explanation/messaging | `architecture/decision-flow.md`, `architecture/privacy-architecture.md` | `backend/services/` (Phase 9) | OpenAI API |
| Evaluation (of #1 and #2) | `ml/evaluation.md` | `ml/evaluation/`, `simulation/evaluation/` | scikit-learn metrics, Qini/uplift curves |
| Learning loop | `ml/learning-loop.md` | `ml/training/`, `backend/workers/` | Batch retrain + validate + promote |
| Training data (`TrainingExample`) | `data/data-model.md` "Training data contract", `ml/labels.md` | `ml/training/` | Phase 1A.4 / ADR-012 — one row per `DecisionRecord × candidate action`; label only on the observed action (no counterfactuals); `RecoveryCase`-level split; reproducible dataset snapshot |
| Model versioning / traceability | `ml/learning-loop.md`, `data/data-model.md` | `ml/models/artifacts/`, DB | `ModelVersion` (Phase 1A.3 / ADR-011) — immutable except `status` (`DRAFT`/`VALIDATED`/`PROMOTED`/`RETIRED`/`REJECTED`), one `PROMOTED` per model role; every `Prediction` references one directly (exact, immutable); a `DecisionRecord`'s model version is derived from its `Prediction`s, not stored independently |

## 4. Alternatives considered

Considered using a single LLM for jobs #1/#2 (i.e. asking GPT to estimate a
recovery probability). Rejected outright — see `ml/models.md` section 4 for
the detailed reasoning (LLMs are not well-suited to calibrated tabular
probability estimation, and using one there would remove the auditability
this product depends on).

## 5. Why this option

Separating these four jobs mirrors exactly how the architecture separates
prediction, decisioning, and explanation (see `architecture/decision-flow.md`).
It also gives the hackathon pitch a clean technical narrative: "we didn't
just wrap an LLM — here are the four distinct AI/ML techniques we used and
why each one is the right tool for its specific job."

## 6. Example

See the worked example in `architecture/decision-flow.md` section 6, which
shows all four jobs contributing to one decision.

## 7. Implementation implications

- **Phase 3 (ADR-013): DONE.** Job #1 is a logistic-regression S-learner
  (`ml/models/`, `ml/training/`, `ml/inference/`) trained from
  `TrainingExample` rows and registered as a `ModelVersion`. Because it
  takes `action` as a treatment feature it also covers job #1b directly.
  A **deterministic** job #3 (`backend/decision_engine/`,
  `backend/policies/`) — EIRV → recommendation → policy veto →
  `DecisionRecord` — is also implemented so the model has a real
  consumer; the ML/uplift refinement of #3's inputs is Phase 4.
- **Phase 4 (ADR-014): DONE.** Job #2 (incremental effect) is
  `incremental(action) = P(recovery|a) − P(recovery|NO_ACTION)`, derived
  at inference from a candidate's per-action probabilities (never stored,
  never an EIRV substitute). Four learners were compared on
  decision-quality metrics vs the simulator oracle; the **T-learner**
  (per-action logistic heads, `ml/models/uplift.py`) was selected and
  feeds job #3 through the unchanged `ModelVersion` / `ml.inference` path.
- Phase 9 implements job #4 (LLM), which never feeds back into #1/#2/#3.

## 8. Open questions

See individual docs (`ml/models.md`, `ml/uplift-modelling.md`) for
job-specific open questions.

## 9. Visual

```
        EVENT
          │
          ▼
   ┌─────────────┐     ┌──────────────┐
   │  ML JOB #1  │────▶│  ML JOB #2   │
   │  baseline + │     │  incremental │
   │  per-action │     │  effect      │
   │  probability│     │  (uplift)    │
   └─────────────┘     └──────┬───────┘
                               ▼
                     ┌───────────────────┐
                     │  OPTIMIZATION #3  │──── policy ────▶ ACTION
                     └───────────────────┘
          ▲
          │ (explains, drafts messages — never decides)
   ┌─────────────┐
   │   LLM #4    │
   └─────────────┘
```
