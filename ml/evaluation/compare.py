"""Observational comparison of Phase 4 candidate models.

Everything here is computed from held-out ``TrainingExample`` rows (the
observed action's outcome per cycle) — **no simulator hidden truth**. This
module imports neither ``simulation.ground_truth`` nor
``simulation.evaluation``. The oracle-based decision-quality report lives
in ``simulation/evaluation/uplift_report.py``.

For each candidate it reports, on the observed rows of the given split:

* Brier score + coarse ECE of the predicted ``P(recovery | features,
  observed_action)`` against the observed outcome;
* ROC-AUC where both classes are present;
* per-action row counts and mean predicted probability (action separation).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ml.data.dataset import TrainingRow
from ml.features.schema import ACTIONS


def _ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        tot += (m.sum() / len(p)) * abs(y[m].mean() - p[m].mean())
    return float(tot)


@dataclass
class ObsMetrics:
    model: str
    n: int
    positive_rate: float
    brier: float | None
    log_loss: float | None
    roc_auc: float | None
    ece: float | None
    mean_proba_by_action: dict[str, float] = field(default_factory=dict)
    n_rows_by_action: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "n": self.n,
            "positive_rate": round(self.positive_rate, 4),
            "brier": _r(self.brier),
            "log_loss": _r(self.log_loss),
            "roc_auc": _r(self.roc_auc),
            "ece": _r(self.ece),
            "mean_proba_by_action": {k: round(v, 4) for k, v in self.mean_proba_by_action.items()},
            "n_rows_by_action": self.n_rows_by_action,
            "notes": self.notes,
        }


def _r(x):
    return None if x is None else round(float(x), 4)


def observational_metrics(model, rows: list[TrainingRow], *, name: str) -> ObsMetrics:
    if not rows:
        return ObsMetrics(name, 0, 0.0, None, None, None, None, notes=["empty split"])

    y = np.asarray([r.label for r in rows], dtype=int)
    p = np.asarray(
        [model.predict(r.feature_snapshot, r.action) for r in rows], dtype=float
    )
    p = np.clip(p, 0.0, 1.0)

    notes: list[str] = []
    roc = ll = brier = ece = None
    if len(np.unique(y)) < 2:
        notes.append("single outcome class in split — ROC-AUC / log-loss skipped")
    else:
        roc = float(roc_auc_score(y, p))
        ll = float(log_loss(y, np.clip(p, 1e-7, 1 - 1e-7), labels=[0, 1]))
        brier = float(brier_score_loss(y, p))
        ece = _ece(y, p)

    by_action_p: dict[str, float] = {}
    by_action_n: dict[str, int] = {}
    for a in ACTIONS:
        preds = [model.predict(r.feature_snapshot, a) for r in rows]
        by_action_p[a] = float(np.mean(preds)) if preds else 0.0
        by_action_n[a] = sum(1 for r in rows if r.action == a)

    return ObsMetrics(
        model=name,
        n=len(rows),
        positive_rate=float(y.mean()),
        brier=brier,
        log_loss=ll,
        roc_auc=roc,
        ece=ece,
        mean_proba_by_action=by_action_p,
        n_rows_by_action=by_action_n,
        notes=notes,
    )
