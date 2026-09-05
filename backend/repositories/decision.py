"""Data access for the Phase 1A.2 decision lifecycle.

One repository for the whole cycle because the pieces are only meaningful
together: a ``DecisionRecord`` and its per-action ``Prediction`` /
``PolicyEvaluation`` rows, an optional ``Intervention`` (RETRY/MESSAGE
only), and an optional ``Outcome`` (delayed OK).

Immutability: predictions, policy evaluations, the decision's
recommendation/final action, and a resolved outcome are write-once. Only
an Intervention's ``execution_status`` / ``resolved_at`` and the
DecisionRecord's ``status`` progress.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.errors import DataContractError
from backend.database.base import utcnow
from backend.models import enums
from backend.models.core_entities import RecoveryCase
from backend.models.decision import (
    DecisionRecord,
    Intervention,
    Outcome,
    PolicyEvaluation,
    Prediction,
)


class DecisionCycleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- DecisionRecord -------------------------------------------------

    def open_cycle(
        self,
        *,
        case: RecoveryCase,
        payment_amount_at_decision,
        decision_timestamp: datetime | None = None,
        policy_id: str | None = None,
        policy_version: str | None = None,
        decision_engine_version: str | None = None,
    ) -> DecisionRecord:
        next_cycle = (
            self.db.scalar(
                select(func.coalesce(func.max(DecisionRecord.cycle_number), 0)).where(
                    DecisionRecord.recovery_case_id == case.id
                )
            )
            + 1
        )
        dr = DecisionRecord(
            recovery_case_id=case.id,
            cycle_number=next_cycle,
            decision_timestamp=decision_timestamp or utcnow(),
            payment_amount_at_decision=payment_amount_at_decision,
            # provisional; set for real by finalize()
            recommended_action=enums.Action.NO_ACTION.value,
            final_action=enums.Action.NO_ACTION.value,
            policy_id=policy_id,
            policy_version=policy_version,
            decision_engine_version=decision_engine_version,
            status=enums.DecisionRecordStatus.DECIDED.value,
        )
        self.db.add(dr)
        self.db.flush()
        return dr

    def add_prediction(
        self,
        *,
        decision_record: DecisionRecord,
        action: str,
        recovery_probability,
        model_version_id: uuid.UUID,
        feature_snapshot: dict,
    ) -> Prediction:
        p = Prediction(
            decision_record_id=decision_record.id,
            case_id=decision_record.recovery_case_id,
            action=action,
            recovery_probability=recovery_probability,
            model_version_id=model_version_id,
            feature_snapshot=feature_snapshot,
        )
        self.db.add(p)
        self.db.flush()
        return p

    def add_policy_evaluation(
        self,
        *,
        decision_record: DecisionRecord,
        action: str,
        policy_id: str,
        policy_version: str,
        result: str,
        reason_code: str | None = None,
        reason: str | None = None,
        evaluated_at: datetime | None = None,
    ) -> PolicyEvaluation:
        pe = PolicyEvaluation(
            decision_record_id=decision_record.id,
            action=action,
            policy_id=policy_id,
            policy_version=policy_version,
            result=result,
            reason_code=reason_code,
            reason=reason,
            evaluated_at=evaluated_at or utcnow(),
        )
        self.db.add(pe)
        self.db.flush()
        return pe

    def finalize(
        self,
        *,
        decision_record: DecisionRecord,
        recommended_action: str,
        final_action: str,
        decision_reason: str | None = None,
        value_context: list | None = None,
    ) -> DecisionRecord:
        decision_record.recommended_action = recommended_action
        decision_record.final_action = final_action
        decision_record.decision_reason = decision_reason
        decision_record.value_context = value_context
        self.db.flush()
        return decision_record

    def set_status(self, decision_record: DecisionRecord, status: str) -> DecisionRecord:
        decision_record.status = status
        self.db.flush()
        return decision_record

    # ---- Intervention -------------------------------------------------

    def record_intervention(
        self,
        *,
        decision_record: DecisionRecord,
        action: str,
        execution_status: str = enums.ExecutionStatus.REQUESTED.value,
        channel: str | None = None,
        provider_ref: str | None = None,
        cost_incurred=0,
        requested_at: datetime | None = None,
    ) -> Intervention:
        """Create the Intervention for a RETRY/MESSAGE final action.

        Rejected for NO_ACTION (Phase 1A.2: NO_ACTION never creates an
        Intervention) and for a mismatch with ``final_action``.
        """
        if decision_record.final_action == enums.Action.NO_ACTION.value:
            raise DataContractError(
                "NO_ACTION never creates an Intervention (Phase 1A.2 / ADR-010)"
            )
        if action not in enums.EXECUTABLE_ACTIONS:
            raise DataContractError(f"Intervention action must be RETRY or MESSAGE, got {action!r}")
        if action != decision_record.final_action:
            raise DataContractError(
                f"Intervention action {action!r} != final_action "
                f"{decision_record.final_action!r}"
            )
        intv = Intervention(
            decision_record_id=decision_record.id,
            case_id=decision_record.recovery_case_id,
            action=action,
            channel=channel,
            execution_status=execution_status,
            provider_ref=provider_ref,
            cost_incurred=cost_incurred,
            requested_at=requested_at or utcnow(),
        )
        self.db.add(intv)
        self.db.flush()
        return intv

    def update_execution_status(
        self,
        intervention: Intervention,
        execution_status: str,
        *,
        resolved_at: datetime | None = None,
    ) -> Intervention:
        intervention.execution_status = execution_status
        if execution_status in {
            enums.ExecutionStatus.ACCEPTED.value,
            enums.ExecutionStatus.REJECTED.value,
            enums.ExecutionStatus.FAILED.value,
        }:
            intervention.resolved_at = resolved_at or utcnow()
        self.db.flush()
        return intervention

    # ---- Outcome ----------------------------------------------------

    def record_outcome(
        self,
        *,
        decision_record: DecisionRecord,
        result: str,
        recovery_amount=0,
        observed_at: datetime | None = None,
        intervention: Intervention | None = None,
    ) -> Outcome:
        """Record the observed result of this decision cycle. Delayed
        outcomes: ``observed_at`` may lag execution. A NO_ACTION cycle
        passes ``intervention=None`` (natural recovery / non-recovery)."""
        if self.db.scalar(
            select(Outcome).where(Outcome.decision_record_id == decision_record.id)
        ):
            raise DataContractError(
                f"DecisionRecord {decision_record.id} already has an Outcome "
                "(immutable once resolved)"
            )
        oc = Outcome(
            decision_record_id=decision_record.id,
            intervention_id=intervention.id if intervention else None,
            result=result,
            recovery_amount=recovery_amount if result == enums.OutcomeResult.RECOVERED.value else 0,
            observed_at=observed_at or utcnow(),
        )
        self.db.add(oc)
        self.db.flush()
        return oc

    # ---- reads -----------------------------------------------------

    def cycles_for_case(self, case_id: uuid.UUID) -> list[DecisionRecord]:
        return list(
            self.db.scalars(
                select(DecisionRecord)
                .where(DecisionRecord.recovery_case_id == case_id)
                .order_by(DecisionRecord.cycle_number)
            )
        )

    def get(self, decision_record_id: uuid.UUID) -> DecisionRecord | None:
        return self.db.get(DecisionRecord, decision_record_id)
