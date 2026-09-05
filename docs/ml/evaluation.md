# Evaluation

## 1. Purpose

Define every metric RecoverAI reports, at both the ML level and the
business level, and fix the mandatory baseline-comparison methodology
(UC-3) that the whole product's credibility rests on.

## 2. Context

Product discussion was explicit that "accuracy" alone is an insufficient
and potentially misleading headline metric for a financial decision system.
This document is the checklist against which any reported result — in the
dashboard, the README, or the pitch — must be justified.

## 3. Current decision

### ML-level metrics (per `ModelVersion`, `ml/evaluation/`)

Every metric below is recorded against the `ModelVersion` it was computed
for (see `data/data-model.md`, `ml/learning-loop.md`), so model-to-model
comparisons and the promotion decision are always version-anchored.


```
AUC                     — ranking quality of recovery probability
Precision / Recall      — at a chosen operating threshold, if a
                          classification decision is ever reported alongside
                          the probability
Brier score, calibration curve, ECE   — see ml/probability-calibration.md
Uplift/Qini-style curve — quality of the incremental-effect ranking,
                          evaluated against simulator ground truth
                          (data/synthetic-data.md)
```

### Business-level / decision-quality metrics (per experiment batch, `simulation/evaluation/` and later the dashboard)

```
Revenue at risk              — total amount across all failed payments in the batch
Revenue recovered            — total amount actually recovered
Recovery rate                — recovered cases / total cases
Incremental revenue          — RecoverAI's recovered revenue MINUS the
                                baseline strategy's recovered revenue, on
                                the SAME batch of cases
Intervention count           — how many actions were taken (fewer, for the
                                same or better revenue, is a genuine win —
                                see product discussion's "efficiency" framing)
Cost per recovery            — total intervention cost / recovered cases
False intervention rate      — interventions where ground-truth incremental
                                effect was ~zero or negative (evaluation-only,
                                requires simulator ground truth)
```

### The mandatory baseline

Every incremental-revenue claim must be computed against a clearly defined,
fixed baseline strategy run on the exact same batch of cases:

```
Baseline (v1): "retry every failed payment exactly once, no other action"
```

This is deliberately simple and matches what many merchants do today
without any recovery intelligence — it is not a strawman, it's a realistic
default. `ml/uplift-modelling.md`'s and `decision-engine/`'s outputs are
always reported *relative to this baseline*, never as an absolute number in
isolation.

### Two distinct ways to measure incremental effect

These must not be conflated:

| Mechanism | What it is | Knows hidden truth? | Used for |
|---|---|---|---|
| **Simulator hidden ground truth** (`data/synthetic-data.md`) | The generator's concealed `natural_recovery_probability` and true per-action effects | Yes — but only `simulation/evaluation/` may read them | Offline proof that predictions/uplift converge toward truth; false-intervention rate |
| **CONTROL vs TREATMENT** (`ExperimentAssignment`, `data/data-model.md`) | An observational split: CONTROL = baseline/no intervention, TREATMENT = decision-policy intervention; compare realized outcomes | No hidden knowledge | Estimating realized incremental recovery the way a real deployment would, without a simulator |

The MVP uses the simulator for its headline evaluation. `ExperimentAssignment`
is the minimal hook that lets the same incremental question be answered
observationally. The exact experiment design, randomization, allocation,
and statistical method are finalized later in this phase — not locked now.

**Case-level splitting (Phase 1A.4 / ADR-012).** Train/validation/test
splits are made at `RecoveryCase` level, never `TrainingExample` level —
all rows derived from a case's decision cycles stay in one split.
Splitting per row would leak correlated same-case observations across
splits and inflate apparent performance. The observational
`TrainingExample` set gives the outcome of the *observed* action per
cycle only — it does **not** supply all counterfactual outcomes; that is
the simulator ground truth's job (evaluation only).

### What counts as a "better model" (high level)

Model improvement is **not** defined solely as higher accuracy or higher
AUC. The evaluation framework should eventually consider several
dimensions together:

```
Predictive quality
+ Probability calibration
+ Treatment/uplift quality
+ Decision quality
+ Incremental expected value
+ Realized incremental recovery
+ Policy compliance
```

The exact metrics, weights, and promotion thresholds are finalized during
the ML phase (Phase 3–7). The governing goal, stated now so later phases
don't drift from it:

> A model should only be promoted if it improves the decision system in a
> meaningful and validated way.

See `ml/learning-loop.md` Step 3 for how this gates promotion. Evaluation
outcome drives the `ModelVersion` lifecycle (Phase 1A.3 / ADR-011):
passing → `VALIDATED`, failing → `REJECTED` (which can never later become
`PROMOTED` — retraining makes a new version), and at most one `PROMOTED`
version per model role is the production default at a time. A `VALIDATED`
candidate may still serve a controlled experiment's `TREATMENT` arm
without becoming the default.

## 4. Alternatives considered

Considered reporting only "recovery rate" as the headline metric (simpler
to explain). Rejected — recovery rate alone can be inflated by intervening
on everyone, including customers who would have paid anyway (exactly the
failure mode `ml/uplift-modelling.md` and `product/problem-statement.md`
warn against). Incremental revenue vs. baseline is the metric that can't be
gamed this way.

## 5. Why this option

This metric set gives three audiences what they each need: engineers get
standard, checkable ML metrics (AUC, calibration); the business/demo
audience gets one clean headline number (incremental revenue) that can't be
misrepresented; and the evaluation-only ground-truth metrics (uplift
curve, false intervention rate) let the team honestly verify the system
before making any public claim.

## 6. Example — reporting format

```
Batch: 10,000 synthetic failed payments (clearly labelled synthetic)

                       Baseline      RecoverAI
Revenue at risk        ₹42.0L        ₹42.0L
Revenue recovered      ₹8.2L         ₹11.7L
Recovery rate          19.5%         27.9%
Interventions          10,000        6,200
Incremental revenue    —             +₹3.5L
Cost per recovery      ₹—            ₹32
```

## 7. Implementation implications

**Phase 3 status (ADR-013):** ML-level predictive metrics are
**implemented** in `ml/evaluation/evaluate.py` — ROC-AUC, log loss, Brier
score, a coarse ECE, and per-action mean-probability separation, computed
from the held-out `TrainingExample` split (observational; **no** simulator
hidden truth). Attached to the `ModelVersion` (`evaluation_summary`) at
training time.

**Phase 4 status (ADR-014):** the full model-comparison harness is
**implemented**. `ml/evaluation/compare.py` = observational metrics
(Brier / ROC-AUC / ECE / per-action separation), imports no simulator
truth. `simulation/evaluation/uplift_report.py` +
`simulation/evaluation/phase4_compare.py` = the **decision-quality**
report against the hidden oracle, using **real persisted decision-time
snapshots** (cycle 1) — no proxy: per-case predicted vs oracle
*incremental* probability (MAE/RMSE), model EIRV-argmax action vs oracle
best action (**action agreement**), and per-case **EIRV regret**
(`oracle_best_EIRV − chosen_EIRV`, hidden-truth scored, fixed ADR-003
formula). Reproducible: `python -m simulation.evaluation.phase4_compare`
(seeds 42/7/123, 1500 cases, 70/15/15 case-level split); artifact under
`simulation/evaluation/artifacts/`. Result: the **T-learner** wins on
every metric and is the selected model (see ADR-014 / `docs/README.md`).
The full batch baseline-vs-RecoverAI revenue table below is still Phase
12.

- `simulation/evaluation/report.py` (Phase 2/3) produces exactly this table
  format from a batch run, so the dashboard (Phase 11) can render the same
  structure directly rather than inventing a new one.
- No dashboard panel, README claim, or pitch slide may state a "recovered"
  or "improved" number without this baseline comparison being available to
  back it up.

## 8. Open questions

- Whether a second, more sophisticated baseline (e.g. "retry twice with a
  fixed delay," closer to Stripe/Chargebee-style smart retry) should also
  be reported alongside the naive baseline, to more fairly show RecoverAI's
  edge over *existing* smart-retry products, not just naive retry — leaning
  yes, as a stretch goal for Phase 12 (End-to-End Demo), not required for
  earlier phases.

## 9. Visual

```
        SAME BATCH OF CASES
           │           │
           ▼           ▼
      BASELINE      RECOVERAI
      strategy      decision engine
           │           │
           ▼           ▼
      recovered_B   recovered_R
           │           │
           └─────┬─────┘
                 ▼
        incremental = recovered_R − recovered_B
```
