"""Phase 4 model comparison against the simulator's HIDDEN ground truth.

This module lives under ``simulation/evaluation/`` — the ONLY sanctioned
reader of hidden per-action potential outcomes
(``docs/data/synthetic-data.md`` §3). It may import ``ml`` and the
``Oracle``; nothing under ``ml/`` or ``backend/`` imports it. Hidden truth
therefore flows strictly *out* to evaluation, never *into* training
features / labels / persisted predictions / production inference.

Given a set of held-out ``RecoveryCase`` ids (the case-level TEST split),
for each case it:

  1. reads the model's per-action ``P(recovery|features,action)`` from a
     REAL persisted decision-time ``feature_snapshot`` (cycle 1) — never a
     proxy;
  2. reads the oracle's hidden ``p_by_action`` for that case's cycle 1;
  3. computes predicted vs oracle *incremental* probabilities, the
     model's EIRV-argmax action vs the oracle's best action, and the
     per-case EIRV regret ``oracle_best_EIRV - chosen_action_EIRV``
     (scored under hidden truth).

The EIRV formula is the fixed Phase 1A / ADR-003 one, via
``backend.decision_engine.value_engine`` — this report does NOT introduce
a second EIRV.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.decision_engine.optimizer import rank_actions
from backend.decision_engine.value_engine import eirv_by_action
from backend.models.decision import DecisionRecord, Prediction
from simulation.config import SimConfig
from simulation.ground_truth.potential_outcomes import PotentialOutcomes, eirv
from simulation.ground_truth.store import GroundTruthStore

_ACTIONS = ("RETRY", "MESSAGE", "NO_ACTION")


@dataclass
class DecisionQuality:
    model: str
    n_cases: int
    # incremental-probability error vs oracle (RETRY & MESSAGE only)
    incr_mae: float
    incr_rmse: float
    incr_mae_by_action: dict[str, float]
    # decision quality
    action_agreement: float
    mean_eirv_regret: float
    total_eirv_regret: float
    model_action_mix: dict[str, float]
    oracle_action_mix: dict[str, float]
    model_realised_eirv: float
    oracle_realised_eirv: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        rd = lambda d: {k: round(v, 4) for k, v in d.items()}
        return {
            "model": self.model,
            "n_cases": self.n_cases,
            "incremental_mae": round(self.incr_mae, 4),
            "incremental_rmse": round(self.incr_rmse, 4),
            "incremental_mae_by_action": rd(self.incr_mae_by_action),
            "action_agreement": round(self.action_agreement, 4),
            "mean_eirv_regret": round(self.mean_eirv_regret, 2),
            "total_eirv_regret": round(self.total_eirv_regret, 2),
            "model_action_mix": rd(self.model_action_mix),
            "oracle_action_mix": rd(self.oracle_action_mix),
            "model_realised_eirv": round(self.model_realised_eirv, 2),
            "oracle_realised_eirv": round(self.oracle_realised_eirv, 2),
            "notes": self.notes,
        }


def _first_cycle_snapshot(db: Session, case_id: str) -> dict | None:
    """The decision-time ``feature_snapshot`` of a case's earliest cycle,
    read from a persisted ``Prediction`` (identical across the 3 actions of
    a cycle)."""
    dr = db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.recovery_case_id == case_id)
        .order_by(DecisionRecord.cycle_number)
        .limit(1)
    )
    if dr is None:
        return None
    pred = db.scalar(
        select(Prediction).where(Prediction.decision_record_id == dr.id).limit(1)
    )
    return dict(pred.feature_snapshot) if pred is not None else None


def _oracle_first_cycle(gt) -> tuple[dict, str] | None:
    if not gt.cycles:
        return None
    c = gt.cycles[0]
    return c.p_by_action, (gt.oracle_best_action or "NO_ACTION")


def build_decision_quality(
    db: Session,
    *,
    run_id: str,
    model,
    model_name: str,
    test_case_ids: list[str],
    cfg: SimConfig | None = None,
) -> DecisionQuality:
    cfg = cfg or SimConfig()
    store = GroundTruthStore.load(run_id)

    incr_abs: list[float] = []
    incr_abs_by_action: dict[str, list[float]] = {"RETRY": [], "MESSAGE": []}
    agree = 0
    regrets: list[float] = []
    model_mix = {a: 0 for a in _ACTIONS}
    oracle_mix = {a: 0 for a in _ACTIONS}
    model_realised = 0.0
    oracle_realised = 0.0
    n = 0
    notes: list[str] = []

    for cid in test_case_ids:
        gt = store.get(cid)
        snap = _first_cycle_snapshot(db, cid)
        if gt is None or snap is None:
            continue
        oc = _oracle_first_cycle(gt)
        if oc is None:
            continue
        p_true, oracle_best = oc
        amount = gt.payment_amount
        po = PotentialOutcomes(
            case_index=0, p_by_action=p_true, regime=gt.cycles[0].regime, amount=amount
        )

        p_hat = model.predict_all_actions(snap)
        # incremental probability error (RETRY, MESSAGE) vs oracle
        for a in ("RETRY", "MESSAGE"):
            true_incr = p_true[a] - p_true["NO_ACTION"]
            pred_incr = p_hat[a] - p_hat["NO_ACTION"]
            e = abs(pred_incr - true_incr)
            incr_abs.append(e)
            incr_abs_by_action[a].append(e)

        # model's economic choice via the FIXED EIRV formula (no ML score)
        eirv_hat = eirv_by_action(p_hat, amount)
        model_choice = rank_actions(eirv_hat)[0]

        model_mix[model_choice] += 1
        oracle_mix[oracle_best] += 1
        if model_choice == oracle_best:
            agree += 1

        chosen_eirv_true = 0.0 if model_choice == "NO_ACTION" else eirv(po, model_choice, cfg=cfg)
        oracle_eirv_true = 0.0 if oracle_best == "NO_ACTION" else eirv(po, oracle_best, cfg=cfg)
        regrets.append(oracle_eirv_true - chosen_eirv_true)
        model_realised += chosen_eirv_true
        oracle_realised += oracle_eirv_true
        n += 1

    if n == 0:
        notes.append("no test cases had both ground truth and a persisted snapshot")

    incr_arr = np.asarray(incr_abs) if incr_abs else np.asarray([0.0])
    mix = lambda d: {k: (v / n if n else 0.0) for k, v in d.items()}
    return DecisionQuality(
        model=model_name,
        n_cases=n,
        incr_mae=float(incr_arr.mean()),
        incr_rmse=float(np.sqrt((incr_arr**2).mean())),
        incr_mae_by_action={
            a: float(np.mean(v)) if v else 0.0 for a, v in incr_abs_by_action.items()
        },
        action_agreement=(agree / n) if n else 0.0,
        mean_eirv_regret=float(np.mean(regrets)) if regrets else 0.0,
        total_eirv_regret=float(np.sum(regrets)) if regrets else 0.0,
        model_action_mix=mix(model_mix),
        oracle_action_mix=mix(oracle_mix),
        model_realised_eirv=model_realised,
        oracle_realised_eirv=oracle_realised,
        notes=notes,
    )
