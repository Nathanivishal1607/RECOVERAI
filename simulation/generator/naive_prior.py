"""Placeholder per-action probability *prior* for simulator Predictions.

Phase 2 has no trained model, but ``TrainingExample`` derivation needs a
``Prediction`` per candidate action. This module supplies a crude,
deterministic estimate computed **only from observable features** — it is
explicitly NOT the hidden ground truth (no case-specific noise, no
regime, no latent reliability) and NOT a trained model. Phase 3+ replaces
it with a real ``ModelVersion``.
"""

from __future__ import annotations

import math

# Coarse public priors by failure category (deliberately different from,
# and blunter than, the hidden ground-truth base rates).
_PRIOR = {
    "TEMPORARY": {"RETRY": 0.55, "MESSAGE": 0.42, "NO_ACTION": 0.28},
    "CUSTOMER_ACTION_REQUIRED": {"RETRY": 0.25, "MESSAGE": 0.55, "NO_ACTION": 0.18},
    "PAYMENT_METHOD_ISSUE": {"RETRY": 0.28, "MESSAGE": 0.50, "NO_ACTION": 0.16},
    "LIMIT_EXCEEDED": {"RETRY": 0.34, "MESSAGE": 0.38, "NO_ACTION": 0.24},
    "UNKNOWN": {"RETRY": 0.30, "MESSAGE": 0.32, "NO_ACTION": 0.20},
}
_ACTIONS = ("RETRY", "MESSAGE", "NO_ACTION")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def naive_prior_probabilities(feature_snapshot: dict) -> dict[str, float]:
    cat = feature_snapshot.get("failure_category", "UNKNOWN")
    base = _PRIOR.get(cat, _PRIOR["UNKNOWN"])
    succ = float(feature_snapshot.get("cust_hist_success_rate", 0.7))
    prev_rec = float(feature_snapshot.get("cust_prev_recovery_rate", 0.4))
    attempt = int(feature_snapshot.get("attempt_number", 1))

    adj = 0.8 * (succ - 0.7) + 0.5 * (prev_rec - 0.4) - 0.15 * max(0, attempt - 1)
    out = {}
    for a in _ACTIONS:
        # logit-shift the category prior by the observable adjustment
        logit = math.log(base[a] / (1 - base[a])) + adj
        out[a] = round(max(0.02, min(0.95, _sigmoid(logit))), 4)
    return out
