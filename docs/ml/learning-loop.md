# Learning Loop

## 1. Purpose

Define precisely how "the model gets better after every case" is
implemented — a requirement the user explicitly asked for — without
retraining on every single transaction, which was explicitly rejected as
unsafe/unstable for a financial system.

## 2. Context

The user's request and the agreed resolution: *every completed case must
contribute to improving the system*, but model weight updates happen in
validated batches, not per-transaction. This document is the authoritative
mechanism.

## 3. Current decision

### What "learning after every case" means (and does not mean)

"Every completed case should contribute outcome information to the learning
system." It does **not** mean "retrain model weights after every individual
transaction." The conceptual loop, end to end:

```
Case
 ↓
Prediction            (per candidate action, stamped with the exact ModelVersion)
 ↓
Decision              (DecisionRecord: recommendation + policy result + final action)
 ↓
Intervention / No Action
 ↓
Outcome               (recorded when the observation window resolves; delayed OK)
 ↓
TrainingExample gen   (Phase 1A.4 / ADR-012: one per DecisionRecord × candidate
 ↓                     action; label ONLY on the observed_action row — a Prediction
 ↓                     is NOT an observed outcome; one case → many rows)
Training Dataset      (grows continuously)
 ↓
Case-level split      (train/val/test split by RecoveryCase, never per row)
 ↓
Batch Retraining
 ↓
Evaluation
 ↓
Model Version         (NEW ModelVersion; DRAFT → VALIDATED, or → REJECTED)
 ↓                     + training_dataset_snapshot_id = the exact row set used
Controlled Promotion  (VALIDATED → PROMOTED only if better; REJECTED can never
                       become PROMOTED — retrain a new version)
```

Explicitly:

- Outcomes are captured **continuously** — 100% of closed cases, as their
  observation windows resolve.
- The training dataset **grows continuously** — each closed case yields
  `TrainingExample`s per `data/data-model.md` "Training data contract"
  (one per `DecisionRecord × candidate action`; label only on the observed
  action; `NO_ACTION` rows valid without an `Intervention`).
- Retraining happens in **controlled batches**, not per transaction.
- The train/val/test split is **at `RecoveryCase` level** (a case's rows
  never span splits).
- A new candidate model is **evaluated before promotion**.
- A worse model **must not** automatically replace a better one.

No MLflow or other model-management infrastructure is added for this — a
lightweight model-versioning concept (Step 4) is sufficient for the
hackathon.

### Step 1 — Immediate recording (after every case, no exceptions)

The moment a `RecoveryCase` reaches a terminal state (`RECOVERED`,
`STOPPED`, or `EXPIRED`; `FAILED` cases have no clean outcome and are
excluded), the full record of **each of its decision cycles** — per
`DecisionRecord`: the per-action `Prediction`s (with feature snapshot +
exact `ModelVersion`), the value context, `recommended_action` /
`final_action`, `PolicyEvaluation`s, the `Intervention` (with
`execution_status`) if any, and the `Outcome` (with `observed_at`) — is
durably persisted (see `data/database-schema.md`, Phase 1A.2 / ADR-010).
`TrainingExample`s are then **derived** from those immutable records
(Phase 1A.4 / ADR-012): one per `DecisionRecord × candidate action`, with
an `outcome_label` **only** on the actually-`observed_action` row — the
other candidates stay `Prediction`s, never manufactured counterfactual
labels. So one case yields several correlated observations; the "one case
≠ one TrainingExample" position (Phase 0.5) holds, and `RecoveryCase`
stays the grouping unit for leakage-safe splitting. This happens for 100%
of such cases; nothing about "the system improving" waits on it.

### Step 2 — Batch retraining trigger

A new candidate model is trained when **either**:

```
- N new closed cases have accumulated since the last training run
  (default N = 1,000 for MVP-scale synthetic demos; tune per data volume), OR
- a fixed schedule elapses (e.g. daily) — whichever comes first.
```

For the hackathon, this can be triggered manually/on-demand to make the
"model improved" narrative demonstrable live, rather than depending on a
real production scheduler.

### Step 3 — Validation before promotion

```
New candidate model
        │
        ▼
Evaluate on a held-out validation set (never seen during training)
        │
        ▼
Compare against CURRENT PRODUCTION model on the same validation set:
   - AUC / calibration (ml/probability-calibration.md)
   - uplift/Qini quality against simulator ground truth (evaluation-only)
        │
   ┌────┴─────┐
   ▼          ▼
BETTER      NOT BETTER
   │          │
   ▼          ▼
PROMOTE    REJECT (log why, keep current model in production)
```

"Better" is **not** defined solely as higher accuracy or higher AUC. A
candidate must not regress calibration even if AUC improves, since a
decision engine relying on miscalibrated probabilities is unsafe
regardless of ranking quality. The evaluation framework should eventually
weigh several dimensions together (exact metrics and thresholds finalized
during the ML phase — see `ml/evaluation.md`):

```
Predictive quality
+ Probability calibration
+ Treatment/uplift quality
+ Decision quality
+ Incremental expected value
+ Realized incremental recovery
+ Policy compliance
```

The governing rule: **a model is promoted only if it improves the decision
system in a meaningful and validated way** — not merely because a single
offline metric moved.

### Step 4 — `ModelVersion` (lightweight, not MLflow, for MVP)

Each trained model is a `ModelVersion` — finalized as an **immutable**
entity in Phase 1A.3 / ADR-011 (only its lifecycle `status` mutates).
Lightweight representation for the MVP:

```
Model artifacts saved as: ml/models/artifacts/recovery-model-v{N}.pkl
ModelVersion record (simple DB table or JSON sidecar file) captures:
   model_version_id, model_role, model_name, version,
   artifact_ref, artifact_checksum, training_dataset_snapshot_id,
   feature_schema_id, training_config, training_pipeline_version,
   status  DRAFT | VALIDATED | PROMOTED | RETIRED | REJECTED,
   created_at, training_set_size, validation_metrics, promoted_at
```

Promotion in step 3 above moves a candidate `DRAFT → VALIDATED →
PROMOTED`; a candidate that fails validation goes `→ REJECTED` and can
**never** later become `PROMOTED` in that same immutable form — a
materially different model is always trained as a **new** `ModelVersion`.
Only **one `PROMOTED` `ModelVersion` per model role** (e.g.
`recovery_prediction`) is the production default at a time; `VALIDATED`
candidates may still exist and be used for a controlled experiment's
treatment arm without touching the default (see
`data/data-model.md` "Model, Policy & Experiment data contract").

Every `Prediction` references the exact, immutable `ModelVersion` that
produced it (`model_version_id`); a `DecisionRecord` does not store its
own independent model-version column — its model reference is *derived*
from the `Prediction`s it links to (Phase 1A.2 / ADR-010) — so any past
decision can still be traced to the exact model. `ModelVersion` may
eventually be accompanied by `feature_schema_version` (itself derived, not
stored on `DecisionRecord`) / `decision_engine_version` (optional
debugging metadata on `DecisionRecord`, not load-bearing) /
`policy_version` (required on `DecisionRecord`, per `PolicyEvaluation`) —
see `data/data-model.md`.

MLflow is explicitly deferred (see `architecture/system-architecture.md`
Alternatives) — only introduced if this lightweight approach becomes
unmanageable, which is not expected at hackathon scale.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Retrain model weights after every single case | Explicitly rejected — unstable, risky for a financial system, and each individual case is too small a sample to safely move a production model's weights (this was the exact correction made when the user proposed "update after every case"). |
| Never retrain; ship one fixed model | Contradicts the explicit requirement that the system improve from experience; also wastes the value of the audit trail being built in Step 1. |
| Online/streaming learning (continuously updating weights) | Same risk as per-transaction retraining, plus far more implementation complexity than a hackathon justifies; batch retrain/validate/promote achieves the stated goal more safely. |
| Full MLflow model registry from day one | Unnecessary infrastructure for the data volumes and team size involved in a hackathon submission — see `system-architecture.md`. |

## 5. Why this option

This is the direct, agreed resolution to the user's request: *record every
outcome immediately (so nothing is lost), but only update the deployed
model after it's been shown, on held-out data, to actually be better* — the
same discipline any responsible ML system uses in production, just without
unneeded infrastructure weight.

## 6. Example

```
Day 1: recovery-model-v1 deployed (trained on initial synthetic batch)
Day 2: 1,200 new cases closed → retrain triggered
       recovery-model-v2 candidate: AUC 0.81 → 0.85, calibration ECE 0.04 → 0.03
       → BETTER on both → promoted
Day 3: 1,050 new cases closed → retrain triggered
       recovery-model-v3 candidate: AUC 0.85 → 0.86, calibration ECE 0.03 → 0.07
       → calibration regressed → REJECTED, v2 remains in production, logged
```

## 7. Implementation implications

**Phase 3 status (ADR-013):** Step 1 (immediate recording) is the Phase 2
simulator + Phase 1B repositories. Step 4 (`ModelVersion`) is
**implemented**: `ml/training/train.py::train_recovery_model` trains a
logistic-regression S-learner from persisted `TrainingExample` rows
(case-level split), writes a joblib artifact + sha256, and creates a
`DRAFT` `ModelVersion` with `training_dataset_snapshot_id`,
`feature_schema_id`, `artifact_ref`/`artifact_checksum`,
`training_config`, `random_seed`, and an `evaluation_summary`.
`python -m ml.cli train --promote` runs `DRAFT → VALIDATED → PROMOTED`
(retiring any incumbent `PROMOTED` for the role first) gated on a minimum
held-out ROC-AUC. **Phase 4 (ADR-014)** added `--kind`
(`s_learner` / `t_learner` / `tree_s_learner` / `lgbm_s_learner`) so any
incremental candidate trains + promotes through this same lifecycle
(`ml/training/uplift.py`), and a reproducible model bake-off
(`python -m simulation.evaluation.phase4_compare`) that ranks candidates
on decision-quality metrics vs the simulator oracle — the **T-learner**
was selected. **Step 2 (automatic N-cases/schedule retrain trigger) and
the full multi-dimensional promotion gate of Step 3 are deferred to Phase
7** — the current gate is a single ROC-AUC threshold, run manually; the
Phase 4 metrics bundle (EIRV regret, action agreement, incremental MAE,
calibration) is what that automated gate should eventually weigh.

**Phase 5 status:** Step 1 (immediate recording) now also runs through the
live application path, not only the simulator:
`backend/services/recovery_flow.py::record_outcome` calls
`TrainingExampleRepository.generate_for_decision_record` for every cycle
of a case the moment it reaches a labellable terminal state
(`RECOVERED` / `STOPPED` / `EXPIRED`), so a case processed via the API
(`POST /decisions/{id}/outcome`) feeds the training set exactly as a
simulated one does — one row per `DecisionRecord × candidate action`,
label only on the observed action. Steps 2–4 are unchanged.

- `ml/training/retrain.py` and `ml/evaluation/compare_models.py` (Phase 7)
  implement steps 2-3; a Celery task or manual script triggers them — no
  need for a dedicated scheduler service at hackathon scale.
- The dashboard (Phase 11) should be able to show "model v2 trained on N
  new cases, promoted on [date], improved [metric] from X to Y" as a
  visible, judge-facing proof point — this was specifically called out as
  something that should be visible, not just true internally.

## 8. Open questions

- Whether to explore a contextual-bandit exploration layer once enough
  logged data exists (see `ml/uplift-modelling.md` open questions) — noted
  as a real v2 direction, deliberately not built for MVP given the safety
  concerns around live exploration on financial actions.

## 9. Visual

```
Every case, always:  TERMINAL → RECORD (immediate, 100% of cases)
                     Prediction + DecisionRecord + Intervention + Outcome
                              │
                              ▼
Batch trigger (N cases or schedule):
                        TRAIN CANDIDATE → new ModelVersion (status=candidate)
                              │
                              ▼
                    VALIDATE vs. PRODUCTION ModelVersion
                              │
                    ┌──────────┴──────────┐
                    ▼                     ▼
                BETTER                NOT BETTER
                    │                     │
                    ▼                     ▼
         PROMOTE (status=production)  REJECT (log, keep current)
```
