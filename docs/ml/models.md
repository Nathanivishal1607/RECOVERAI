# Models

## 1. Purpose

Specify which models RecoverAI trains, in what order of complexity, and
exactly why an LLM is not used for the probability-prediction job.

## 2. Context

Per `ml/ml-overview.md`, "ML Job #1" is baseline + per-action recovery
probability prediction. This document fixes the modelling approach for that
job (Phase 3), building toward uplift modelling (Phase 4, `ml/uplift-modelling.md`).

## 3. Current decision

### Progression

```
Step 1 (Phase 3) — Baseline model
   Logistic Regression, predicting P(recovered=1 | features, action=NO_ACTION)
   Purpose: simplest possible, interpretable, fast to validate against the
   simulator's ground truth.

Step 2 (Phase 3) — Candidate model
   LightGBM (gradient boosted trees), same target, richer feature
   interactions, better performance on structured/tabular data expected.
   Compared against Step 1 on held-out data before being trusted.

Step 3 (Phase 3/4) — Per-action extension
   Same model family (LightGBM), `candidate_action` included as an input
   feature → single model predicts P(recovered | features, action=a) for
   any a. Chosen over "one model per action" — see Alternatives.

Step 4 (Phase 4) — Uplift/incremental estimation
   Built from Step 3's per-action predictions (see ml/uplift-modelling.md
   for the full methodology). For MVP this is an **S-learner-style shared
   outcome model with intervention/action represented as a treatment
   feature**: compare P(recovered | action=a) vs P(recovered |
   action=NO_ACTION) from the same model. This is a starting point, not a
   permanent commitment — Phase 4 experimentally compares this against a
   T-learner and other suitable uplift/treatment-effect approaches, and the
   final choice is made on evaluation results, not on terminology or
   complexity (see ml/uplift-modelling.md).
```

Training input: the model consumes `TrainingExample` rows (Phase 1A.4 /
ADR-012) — one per `DecisionRecord × candidate action`, giving a clean
`(features, action/treatment, observed outcome)` triple. Only the
actually-`observed_action` row is labelled; the S-learner (and any
T-learner / uplift-tree comparison in Phase 4) trains on those real rows,
never on counterfactual labels. Splitting is at `RecoveryCase` level.

### The model produces a Prediction, not a Recommendation

The recovery model's only job is **Prediction** — "what is likely to
happen" (`P(recover | features, action)` per candidate action). It does
**not** choose an action. The **Recommendation** ("what should we do")
comes from the decision engine's EIRV + optimizer step, and the
**Execution** ("what was actually done") is gated by the policy engine.
The recommended action can differ from the executed action. These three
are recorded distinctly — see `data/data-model.md` "Prediction vs.
Recommendation vs. Execution" and the `DecisionRecord` concept.

Contract (Phase 1A.2 / ADR-010): a `Prediction` is **per decision cycle
and per candidate action** — one `Prediction` for `RETRY`, one for
`MESSAGE`, one for `NO_ACTION` (the baseline) inside each `DecisionRecord`
— and each is stamped with the **exact immutable `ModelVersion`** that
produced it (not `model_name` alone), so any historical decision is
traceable to the precise model even after promotion. See
`data/data-model.md` "Decision data contract".

### Why not an LLM for probability prediction

Explicitly decided against, for concrete reasons:

- Our inputs are structured/tabular (amount, method, failure category,
  historical rates) — exactly the data shape gradient-boosted trees excel
  at, and LLMs do not have a demonstrated advantage on.
- Financial decisioning requires **calibrated** probabilities (see
  `ml/probability-calibration.md`) — an LLM asked to output "82%" has no
  guarantee that its 82%-labelled predictions are actually correct 82% of
  the time; a properly calibrated ML classifier does, and can be measured.
- Reproducibility and auditability: a versioned LightGBM model with a fixed
  feature snapshot will produce the identical prediction if re-run; this is
  required for the audit trail (`architecture/security-and-safety.md`) and
  is not a property LLM outputs reliably have at low cost.
- Speed/cost: scoring thousands of cases through a local tabular model is
  far cheaper and faster than an LLM call per case.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| One separate model per action (3 models: baseline, retry, message) | Requires 3x the labelled data per segment to reach the same statistical power, since each model only sees examples where that specific action was taken; a single model with `action` as a feature shares statistical strength across actions and directly supports the uplift comparison in one place. |
| Deep learning (neural net) on tabular data | No demonstrated advantage over gradient boosting for this data size/shape, adds training complexity and reduces interpretability — rejected per the instruction not to introduce deep learning without a demonstrated reason. |
| LLM-based probability estimation | See section 3 above — rejected for calibration, reproducibility, cost, and appropriateness-of-tool reasons. |

## 5. Why this option

Starting with logistic regression, only moving to LightGBM once it's shown
to actually beat the baseline on held-out synthetic data, keeps the project
honest about complexity being earned rather than assumed — directly
matching the instruction to "not immediately implement complicated models."

## 6. Example

```
Input: {amount: 500000, payment_method: "UPI", failure_category: "TIMEOUT",
        attempt_number: 1, historical_success_rate: 0.93, ...,
        candidate_action: "MESSAGE"}
Output: P(recovered=1) = 0.67
```

## 7. Implementation implications

**Phase 3 status (ADR-013): IMPLEMENTED.** Step 1 (baseline model) is
shipped as `ml/models/recovery_model.py::RecoveryModel` — a scikit-learn
`Pipeline(StandardScaler → LogisticRegression)` over
`[sim-feature-schema-v1 features ⊕ one-hot(candidate action)]`. It is the
S-learner (action as a treatment feature) directly, i.e. Steps 1 and 3
are realized together as the MVP model. Inference is
`ml/inference/recovery.py` — `RecoveryInference.predict(features, action)
-> float` and `predict_all_actions(snapshot)`, loaded from an immutable
`ModelVersion` and called once per candidate action by
`backend/decision_engine/orchestrator.py`.

**Phase 4 status (ADR-014): IMPLEMENTED.** Step 2 (LightGBM) and Step 4
(uplift) are done. `ml/models/uplift.py` builds four candidates behind a
common `IncrementalModel` interface — `s_learner`, `t_learner`
(per-action logistic heads), `tree_s_learner` (shallow sklearn tree),
`lgbm_s_learner` (deterministic LightGBM) — compared on predictive +
decision-quality metrics against the simulator oracle
(`simulation/evaluation/phase4_compare.py`). **The T-learner was selected**
(best EIRV regret / action agreement) and is trained + promoted through
the same `ModelVersion` lifecycle and the same `ml.inference` path — the
Decision Engine is unchanged. A calibration wrapper and a true EconML
uplift tree remain deferred.

- `ml/inference/recovery.py` exposes the stable inference API:
  `predict(features: dict, action: str) -> float`, called once per
  candidate action by the decision engine (`decision-engine/decision-engine.md`).
- Model artifacts are saved as `ml/models/artifacts/<name>-<version>.joblib`
  (Phase 3) with a sha256 checksum, and resolve to a `ModelVersion`
  record — **immutable** except its lifecycle `status`
  (`DRAFT`/`VALIDATED`/`PROMOTED`/`RETIRED`/`REJECTED`), with exactly one
  `PROMOTED` version per model role at a time (Phase 1A.3 / ADR-011).
  **Every `Prediction` references it directly** (`model_version_id`, exact
  and immutable); a `DecisionRecord` does **not** independently store its
  own `model_version_id` — its model reference is **derived** from the
  `Prediction`s it links to (Phase 1A.2 / ADR-010) — see
  `ml/learning-loop.md` for promotion rules and `data/data-model.md` for
  the `ModelVersion` concept.

## 8. Open questions

- Whether LightGBM's advantage over logistic regression will be large
  enough on our (initially modest-sized) synthetic dataset to justify its
  added complexity for the MVP demo — to be measured empirically in Phase 3;
  if not clearly better, logistic regression may remain the shipped model
  with LightGBM documented as the natural next step.

## 9. Visual

```
features + candidate_action
            │
            ▼
   ┌─────────────────────┐
   │  RECOVERY MODEL      │   (Logistic Regression → LightGBM,
   │  (single, versioned) │    action as input feature)
   └──────────┬───────────┘
              │
              ▼
     P(recovered | features, action)
              │
   compared across actions in ──► ml/uplift-modelling.md
```
