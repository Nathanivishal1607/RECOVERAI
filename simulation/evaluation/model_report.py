"""Decision-quality report: trained model + real Decision Engine vs. the
simulator oracle's hidden per-action truth.

This module lives under ``simulation/evaluation/`` — the *only* sanctioned
reader of hidden ground truth (``docs/data/synthetic-data.md`` §3). It may
import ``ml.inference`` and the ``Oracle``; nothing under ``ml/`` or
``backend/`` imports it, so hidden truth still flows strictly *out* to
evaluation and never *into* training features / labels / persisted
predictions.

What it measures, per recovery case in a run:

* the Decision Engine's recommended action (model probs -> EIRV -> argmax),
  using a leakage-free decision-time snapshot;
* the oracle's EIRV-optimal action under hidden truth;
* agreement rate, and the realised-incremental-value gap vs. always
  following the oracle.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from simulation.config import SimConfig
from simulation.evaluation.oracle import Oracle
from simulation.ground_truth.potential_outcomes import PotentialOutcomes, eirv

from backend.decision_engine.optimizer import rank_actions
from backend.decision_engine.value_engine import eirv_by_action
from ml.inference.recovery import RecoveryInference

_ACTIONS = ("RETRY", "MESSAGE", "NO_ACTION")


@dataclass
class ModelDecisionReport:
    n_cases: int
    model_action_distribution: dict[str, int]
    oracle_action_distribution: dict[str, int]
    agreement_rate: float
    model_realised_incremental_value: float
    oracle_realised_incremental_value: float

    def as_dict(self) -> dict:
        return {
            "n_cases": self.n_cases,
            "model_action_distribution": self.model_action_distribution,
            "oracle_action_distribution": self.oracle_action_distribution,
            "agreement_rate": self.agreement_rate,
            "model_realised_incremental_value": self.model_realised_incremental_value,
            "oracle_realised_incremental_value": self.oracle_realised_incremental_value,
        }


def _snapshot_from_ground_truth(gt) -> dict:
    """A minimal leakage-free decision-time snapshot for cycle 1.

    Only observable fields the ground-truth record happens to carry
    (failure category, amount). Everything else falls back to the feature
    schema's neutral defaults inside ``vectorize`` — this is a coarse
    proxy report, not the training path.
    """
    return {
        "failure_category": gt.failure_category,
        "amount": gt.payment_amount,
        "_feature_schema_id": "sim-feature-schema-v1",
    }


def build_report(
    *,
    run_id: str,
    predictor: RecoveryInference,
    cfg: SimConfig | None = None,
) -> ModelDecisionReport:
    cfg = cfg or SimConfig()
    oracle = Oracle.for_run(run_id, cfg)
    store = oracle._store  # sanctioned: this module is the ground-truth reader

    model_actions: Counter = Counter()
    oracle_actions: Counter = Counter()
    agree = 0
    n = 0
    model_riv = 0.0
    oracle_riv = 0.0

    for gt in store._by_case.values():
        if not gt.cycles:
            continue
        n += 1
        first = gt.cycles[0]
        po = PotentialOutcomes(
            case_index=0,
            p_by_action=first.p_by_action,
            regime=first.regime,
            amount=gt.payment_amount,
        )

        snap = _snapshot_from_ground_truth(gt)
        probs = predictor.predict_all_actions(snap)
        eirv_est = eirv_by_action(probs, gt.payment_amount)
        model_choice = rank_actions(eirv_est)[0]
        oracle_choice = gt.oracle_best_action or "NO_ACTION"

        model_actions[model_choice] += 1
        oracle_actions[oracle_choice] += 1
        if model_choice == oracle_choice:
            agree += 1

        # realised incremental value if you *took* each choice, scored by hidden truth
        if model_choice != "NO_ACTION":
            model_riv += eirv(po, model_choice, cfg=cfg)
        if oracle_choice != "NO_ACTION":
            oracle_riv += eirv(po, oracle_choice, cfg=cfg)

    return ModelDecisionReport(
        n_cases=n,
        model_action_distribution=dict(model_actions),
        oracle_action_distribution=dict(oracle_actions),
        agreement_rate=round(agree / n, 4) if n else 0.0,
        model_realised_incremental_value=round(model_riv, 2),
        oracle_realised_incremental_value=round(oracle_riv, 2),
    )
