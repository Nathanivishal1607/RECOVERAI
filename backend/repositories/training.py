"""Phase 1A.4 training-data derivation (ADR-012).

``generate_for_decision_record`` turns one resolved decision cycle into
its ``TrainingExample`` rows — one per candidate action — labelling ONLY
the actually-observed action. No manufactured counterfactual labels.

``split_by_case`` demonstrates the mandatory case-level splitting: every
row of a RecoveryCase lands in exactly one split.
"""

from __future__ import annotations

import hashlib
import random
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.errors import DataContractError
from backend.models import enums
from backend.models.core_entities import RecoveryCase
from backend.models.decision import DecisionRecord, Outcome
from backend.models.training import TrainingExample
from backend.repositories.governance import ExperimentRepository

_ALL_ACTIONS = [enums.Action.RETRY.value, enums.Action.MESSAGE.value,
                enums.Action.NO_ACTION.value]


class TrainingExampleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- derivation -------------------------------------------------

    def generate_for_decision_record(
        self, decision_record: DecisionRecord
    ) -> list[TrainingExample]:
        """Idempotent. Returns [] if the cycle isn't ready (no resolved
        outcome, or its case ended in FAILED)."""
        existing = list(
            self.db.scalars(
                select(TrainingExample).where(
                    TrainingExample.decision_record_id == decision_record.id
                )
            )
        )
        if existing:
            return existing

        case = self.db.get(RecoveryCase, decision_record.recovery_case_id)
        outcome = self.db.scalar(
            select(Outcome).where(Outcome.decision_record_id == decision_record.id)
        )
        case_labellable = (
            case is not None
            and case.status in enums.LABELLABLE_TERMINAL_STATUSES
        )
        if outcome is None or not case_labellable:
            return []  # not final yet

        predictions = {p.action: p for p in decision_record.predictions}
        if set(predictions) != set(_ALL_ACTIONS):
            raise DataContractError(
                f"DecisionRecord {decision_record.id} is missing per-action "
                f"predictions: have {sorted(predictions)}"
            )

        intervention = decision_record.intervention
        final_action = decision_record.final_action

        # observed_action = what actually happened (NOT the recommendation).
        if final_action == enums.Action.NO_ACTION.value:
            observed_action = enums.Action.NO_ACTION.value
            clean_exposure = True  # NO_ACTION exposure is always "clean"
        else:
            observed_action = final_action
            clean_exposure = (
                intervention is not None
                and intervention.execution_status
                == enums.ExecutionStatus.ACCEPTED.value
            )

        arm = ExperimentRepository(self.db).arm_for_case(decision_record.recovery_case_id)
        model_version_id = predictions[final_action].model_version_id

        rows: list[TrainingExample] = []
        for action in _ALL_ACTIONS:
            is_observed = (action == observed_action) and clean_exposure
            te = TrainingExample(
                decision_record_id=decision_record.id,
                recovery_case_id=decision_record.recovery_case_id,
                action=action,
                observed_action=observed_action,
                is_observed=is_observed,
                # features frozen AS OF the decision (from that action's Prediction)
                feature_snapshot=predictions[action].feature_snapshot,
                # label ONLY for the observed action (no counterfactuals)
                outcome_label=outcome.result if is_observed else None,
                recovery_amount=(
                    outcome.recovery_amount
                    if is_observed
                    and outcome.result == enums.OutcomeResult.RECOVERED.value
                    else None
                ),
                observation_timestamp=outcome.observed_at if is_observed else None,
                experiment_arm=arm,
                model_version_id=model_version_id,
            )
            self.db.add(te)
            rows.append(te)
        self.db.flush()
        return rows

    def list_for_case(self, case_id: uuid.UUID) -> list[TrainingExample]:
        return list(
            self.db.scalars(
                select(TrainingExample).where(
                    TrainingExample.recovery_case_id == case_id
                )
            )
        )

    def all(self) -> list[TrainingExample]:
        return list(self.db.scalars(select(TrainingExample)))

    # ---- case-level splitting ------------------------------------

    def split_by_case(
        self,
        *,
        seed: int = 42,
        ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
    ) -> dict[str, list[TrainingExample]]:
        """Assign every RecoveryCase (not every row) to train/val/test, so
        all rows of a case share one split. Deterministic given ``seed``."""
        assert abs(sum(ratios) - 1.0) < 1e-9, "ratios must sum to 1"
        rows = self.all()
        case_ids = sorted({str(r.recovery_case_id) for r in rows})
        rng = random.Random(seed)
        rng.shuffle(case_ids)
        n = len(case_ids)
        n_train = int(n * ratios[0])
        n_val = int(n * ratios[1])
        assignment = {}
        for i, cid in enumerate(case_ids):
            if i < n_train:
                assignment[cid] = "train"
            elif i < n_train + n_val:
                assignment[cid] = "val"
            else:
                assignment[cid] = "test"
        out: dict[str, list[TrainingExample]] = {"train": [], "val": [], "test": []}
        for r in rows:
            out[assignment[str(r.recovery_case_id)]].append(r)
        return out

    # ---- dataset snapshot identity -------------------------------

    def snapshot_id(self, examples: list[TrainingExample]) -> str:
        """Deterministic content hash of a training set — the reproducible
        ``ModelVersion.training_dataset_snapshot_id`` (Phase 1A.4). No
        dataset registry; just a stable identifier."""
        parts = sorted(
            f"{e.decision_record_id}:{e.action}:{e.is_observed}:{e.outcome_label}"
            for e in examples
        )
        digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
        return f"tds-{len(examples)}-{digest[:16]}"
