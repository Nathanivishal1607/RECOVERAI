# Labels

## 1. Purpose

Define exactly what the models are trained to predict — the target
variable(s) — separate from the input features (`ml/features.md`).

## 2. Context

Because RecoverAI needs both a baseline (no-action) recovery probability and
per-action recovery probabilities, there are two related but distinct label
definitions to get right, plus the derived uplift label used in
`ml/uplift-modelling.md`.

## 3. Current decision

### Label 1 — Recovery outcome (binary)

```
recovered ∈ {0, 1}
```

Sourced from the `Outcome.result` field — `RECOVERED` → `1`,
`NOT_RECOVERED` → `0` (see `data/data-model.md` "Decision data contract").
The label is set only for the **observed action** of a decision cycle
(see "Training data construction" below); it is **never** `RecoveryCase.status`
and **never** `Intervention.execution_status`.

### Label 2 — Recovered amount (secondary/regression target, optional for MVP)

```
recovered_amount (numeric, 0 if not recovered)
```

Not required for the MVP decision engine (which works off probability ×
amount), but useful for evaluation reporting (`ml/evaluation.md`) and kept
available for a future refinement (e.g. partial recovery in installments).

### How action is incorporated

Two viable designs, both compatible with this label definition:

```
(a) Single model, `candidate_action` as an input feature:
    predict P(recovered=1 | features, action=a) for each a in {NO_ACTION, RETRY, MESSAGE}

(b) One model per action (three separate models)
```

Decision on (a) vs (b) is made in `ml/models.md` — this document only fixes
that the label itself (`recovered`) is the same regardless of which
modelling approach is used.

### Training data construction (finalized — Phase 1A.4 / ADR-012)

The full contract is in `data/data-model.md` "Training data contract";
the label-relevant rules:

- **Observation unit = `TrainingExample` = one `DecisionRecord` × one
  candidate action** (`RETRY` / `MESSAGE` / `NO_ACTION`). One `RecoveryCase`
  → many rows (one set per decision cycle). **Not** "one case = one row".
- **The label (`recovered`) is set only for the `observed_action`.** In a
  cycle where `final_action = RETRY`, we observed the outcome under `RETRY`
  only — the `MESSAGE` and `NO_ACTION` rows for that cycle carry **no**
  label (their model value stays a *prediction*, never a manufactured
  counterfactual). Never write three `recovered = 1` rows because one
  `RETRY` recovered.
- **`observed_action` is what actually happened**, not the recommendation:
  `recommended=RETRY, policy=BLOCKED, final=NO_ACTION` ⇒
  `observed_action = NO_ACTION`.
- **`NO_ACTION` produces a valid labelled row** when the cycle's `Outcome`
  resolves — no `Intervention` required (`RECOVERED` = natural recovery,
  `NOT_RECOVERED` otherwise).
- **Failed execution** (`final_action` `RETRY`/`MESSAGE` but
  `execution_status ∈ {REJECTED, FAILED}`) is **not** a clean observed
  treatment — that row is not labelled as an observed `RETRY`/`MESSAGE`
  outcome.
- **Eligibility for a label:** valid `DecisionRecord` + known action
  context + a **resolved, usable `Outcome`**. Excludes incomplete/
  unresolved cases, `FAILED` `RecoveryCase`s, policy-only/simulated
  evaluations with no outcome, and duplicates. Terminal-case rule below is
  unchanged.
- **Delayed outcomes:** a row is not final until its observation window
  resolves (`Outcome.observed_at` set, or window closed `NOT_RECOVERED`).
- **Leakage:** the feature vector is the `Prediction.feature_snapshot`
  frozen **as of the `DecisionRecord`**. The `Outcome` is the label,
  never a feature; no later `Outcome`/`recovery_amount`/`observed_at`,
  future `DecisionRecord`/`PaymentEvent`, or later intervention result may
  enter the features.
- **Splitting is at `RecoveryCase` level**, never `TrainingExample` level
  — all rows of a case go in one split (train/val/test). `TrainingExample`
  is the observation unit; `RecoveryCase` is the grouping unit for
  leakage-safe evaluation.

Terminal-case rule (unchanged): a cycle contributes labelled rows only
once its case is in a terminal state with a known outcome — `RECOVERED`,
`STOPPED`, or `EXPIRED`; `FAILED` cases are excluded; open/in-progress
cases are excluded. See the `RecoveryCase` state machine in
`data/data-model.md`.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Multi-class label (recovered same-day / recovered late / not recovered) | Adds complexity without a corresponding decision-engine need in MVP — the optimizer only needs P(recovered), not time-to-recovery, for its core calculation. Time-to-recovery can be added as a secondary metric later without changing this label. |
| Label recovery only if it happened *because of* the action (i.e. bake causality into the label itself) | This is exactly what the uplift model (`ml/uplift-modelling.md`) computes from *two* correlational labels (with-action vs. without-action), not something to hand-encode into a single label — doing so here would just be an unprincipled shortcut around the real uplift problem. |

## 5. Why this option

A simple binary `recovered` label, computed consistently and only from
closed cases, is the cleanest foundation for both the baseline model and
the per-action models — it keeps `ml/models.md` and `ml/uplift-modelling.md`
each solving one well-defined problem instead of conflating labelling
with causal estimation.

## 6. Example

```
Case RC-10281, DecisionRecord D1:
  Predictions: RETRY 0.72, MESSAGE 0.51, NO_ACTION 0.19
  final_action = RETRY,  Intervention ACCEPTED,  Outcome = RECOVERED (₹5,000)
  → TrainingExample(action=RETRY):     observed, recovered = 1, recovery_amount = 5000
  → TrainingExample(action=MESSAGE):   NOT observed → NO label (MESSAGE stays a prediction)
  → TrainingExample(action=NO_ACTION): NOT observed → NO label

Case RC-10282, D1: recommended RETRY, policy BLOCKED, final_action = NO_ACTION,
  Outcome = NOT_RECOVERED (window elapsed)
  → TrainingExample(action=NO_ACTION): observed, recovered = 0   (no Intervention needed)
  → RETRY / MESSAGE rows: no label

Case RC-10283: still WAITING_FOR_OUTCOME → no labelled rows yet (delayed outcome)
```

## 7. Implementation implications

- `ml/training/build_training_set.py` (Phase 3) filters to terminal cases
  with a known outcome (`status IN ('RECOVERED','STOPPED','EXPIRED')`,
  excluding `FAILED`), then emits one `TrainingExample` per
  `(DecisionRecord, candidate action)`, labelling **only** the
  `observed_action` row from `Outcome.result`, and carrying
  `recovery_case_id` for case-level splitting.
- Train/val/test split by `recovery_case_id` (a `GroupShuffleSplit`-style
  split), never by `TrainingExample` id.
- The synthetic generator (`data/synthetic-data.md`) must simulate the same
  filtering/labelling logic so offline evaluation matches how real training
  data is constructed.

## 8. Open questions

- Exact `case_expiry_days` default (currently 14, per
  `architecture/security-and-safety.md`) may need tuning once the simulator
  shows typical recovery-time distributions.

## 9. Visual

```
RecoveryCase (terminal: RECOVERED / STOPPED / EXPIRED; FAILED excluded)
   └── DecisionRecord D1 ──┬── TrainingExample(RETRY)      ─┐
       │                   ├── TrainingExample(MESSAGE)     ├─ label ONLY on the
       │                   └── TrainingExample(NO_ACTION)   ─┘  observed_action row
       └── DecisionRecord D2 ── (same, one set per cycle)
   ▲
   └─ all TrainingExamples of a case stay in ONE train/val/test split
```
