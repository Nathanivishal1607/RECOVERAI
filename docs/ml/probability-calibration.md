# Probability Calibration

## 1. Purpose

Ensure that a predicted "70% recovery probability" actually means what it
says — a prerequisite for the incremental value engine's arithmetic
(`decision-engine/value-calculation.md`) to be trustworthy at all.

## 2. Context

Product discussion flagged this explicitly: an uncalibrated model can have
good ranking ability (AUC) while being systematically over- or
under-confident, which would silently corrupt every downstream ₹-value
calculation, even if the model "looks accurate" by standard classification
metrics.

## 3. Current decision

### What we measure

```
Brier score              — mean squared error between predicted probability
                            and actual outcome (0/1); lower is better.
Calibration curve         — bin predictions (e.g. 0-10%, 10-20%, ...),
                            compare mean predicted probability per bin
                            against actual observed recovery rate per bin.
Expected Calibration Error (ECE) — weighted average gap between predicted
                            and observed probability across bins.
```

### What we do if a model is miscalibrated

```
Apply a post-hoc calibration method (Platt scaling or isotonic regression,
both available in scikit-learn) on a held-out calibration split, and use
the calibrated output — not the raw model output — everywhere downstream
(decision engine, dashboard).
```

### Where this lives

`ml/evaluation/calibration.py` computes these metrics as part of every
model evaluation run (Phase 3 onward); `ml/models/` model artifacts store
whichever variant (raw or calibrated) is actually used for inference.

## 4. Alternatives considered

Considered skipping formal calibration and relying on LightGBM's default
output, reasoning "gradient boosted trees are often reasonably calibrated
already." Rejected as a starting assumption — this must be *verified*, not
assumed, given how directly probability magnitude (not just ranking) feeds
into a ₹-value calculation. If verification shows the raw output is already
well-calibrated, that's a valid finding to document, not a reason to skip
measuring it.

## 5. Why this option

Brier score + calibration curve + ECE are the standard, well-understood
toolkit for this exact problem, all directly available in scikit-learn with
no extra dependencies — consistent with keeping the ML stack lean.

## 6. Example

```
Bin: predicted 60-70%     mean predicted = 0.65
     Observed recovery rate in this bin = 0.51
     → Gap = 0.14 → this bin is over-confident; if consistent across bins,
       apply isotonic regression and re-measure.
```

## 7. Implementation implications

- Every reported model metric in `ml/evaluation.md` output must include
  calibration alongside AUC/precision/recall — accuracy-only reporting is
  explicitly insufficient for this product (per instructions section 16:
  "We care about... probability calibration... Not merely model accuracy.").
- The decision engine must consume calibrated probabilities specifically —
  this should be enforced by `ml/inference/` only ever exposing the
  calibrated prediction function, never the raw model's `.predict_proba()`
  directly.

## 8. Open questions

- Calibration quality depends on having enough data per probability bin;
  with a modest hackathon-scale synthetic dataset, bins may be sparse —
  acceptable for MVP as long as this limitation is documented alongside
  results, not hidden.

## 9. Visual

```
Raw model output ──► calibration check (Brier, curve, ECE)
                            │
              ┌──────────────┴───────────────┐
              ▼                               ▼
      Well-calibrated                  Miscalibrated
      (use as-is)                      → apply Platt/isotonic
                                        → re-check → use calibrated output
```
