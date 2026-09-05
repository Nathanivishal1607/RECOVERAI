"""Observational evaluation of :class:`RecoveryModel`.

Inputs: a fitted model + held-out ``TrainingRow``s (the observed action's
recovery outcome per cycle). No hidden simulator truth is used here.

Reports:

* **Predictive** — ROC-AUC, log loss, Brier score, and a coarse
  Expected Calibration Error (ECE), each guarded for degenerate cases
  (single-class holdout, empty split).
* **Action separation** — mean predicted ``P(recovery|action)`` for each
  of RETRY / MESSAGE / NO_ACTION over the *same* held-out feature
  snapshots, i.e. whether the model produces materially different
  probabilities per action (a prerequisite for a useful EIRV ranking).
  This is a model-behaviour check, not a ground-truth check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ml.data.dataset import TrainingRow
from ml.features.schema import ACTIONS
from ml.models.recovery_model import RecoveryModel


@dataclass
class ActionSeparation:
    mean_proba: dict[str, float]
    spread: float  # max mean - min mean across actions

    def as_dict(self) -> dict:
        return {"mean_proba": self.mean_proba, "spread": self.spread}


@dataclass
class EvalReport:
    n: int
    positive_rate: float | None
    roc_auc: float | None
    log_loss: float | None
    brier: float | None
    ece: float | None
    action_separation: ActionSeparation | None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "positive_rate": self.positive_rate,
            "roc_auc": self.roc_auc,
            "log_loss": self.log_loss,
            "brier": self.brier,
            "ece": self.ece,
            "action_separation": (
                self.action_separation.as_dict() if self.action_separation else None
            ),
            "notes": self.notes,
        }


def _ece(y_true: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        mask = idx == b
        if not mask.any():
            continue
        conf = p[mask].mean()
        acc = y_true[mask].mean()
        total += (mask.sum() / len(p)) * abs(acc - conf)
    return float(total)


def predictive_metrics(model: RecoveryModel, rows: list[TrainingRow]) -> EvalReport:
    notes: list[str] = []
    if not rows:
        return EvalReport(0, None, None, None, None, None, None, ["empty split"])

    y = np.asarray([r.label for r in rows], dtype=int)
    p = np.asarray(
        [model.predict(r.feature_snapshot, r.action) for r in rows], dtype=float
    )
    pos_rate = float(y.mean())

    roc = ll = brier = ece = None
    if len(np.unique(y)) < 2:
        notes.append(
            "holdout has a single outcome class — ROC-AUC / log-loss skipped"
        )
    else:
        roc = float(roc_auc_score(y, p))
        ll = float(log_loss(y, np.clip(p, 1e-7, 1 - 1e-7), labels=[0, 1]))
        brier = float(brier_score_loss(y, p))
        ece = _ece(y, p)

    return EvalReport(
        n=len(rows),
        positive_rate=pos_rate,
        roc_auc=roc,
        log_loss=ll,
        brier=brier,
        ece=ece,
        action_separation=action_separation(model, rows),
        notes=notes,
    )


def action_separation(
    model: RecoveryModel, rows: list[TrainingRow]
) -> ActionSeparation:
    """Mean predicted probability per action over the same snapshots."""
    snaps = [r.feature_snapshot for r in rows]
    means: dict[str, float] = {}
    for a in ACTIONS:
        preds = [model.predict(s, a) for s in snaps]
        means[a] = float(np.mean(preds)) if preds else 0.0
    spread = (max(means.values()) - min(means.values())) if means else 0.0
    return ActionSeparation(mean_proba=means, spread=float(spread))


def evaluate_model(
    model: RecoveryModel,
    *,
    rows_val: list[TrainingRow],
    rows_test: list[TrainingRow],
) -> dict:
    """Full observational report: validation + test."""
    return {
        "validation": predictive_metrics(model, rows_val).as_dict(),
        "test": predictive_metrics(model, rows_test).as_dict(),
    }
