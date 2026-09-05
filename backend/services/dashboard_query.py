"""Phase 6 — read-only aggregation/assembly for the dashboard API.

Mirrors the style of ``backend.schemas.audit``: plain functions that read
existing ORM rows through SQLAlchemy and existing repositories, and compose
them into the Phase 6 response schemas. No new tables, no new write paths.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import enums
from backend.models.core_entities import Payment, RecoveryCase
from backend.models.decision import DecisionRecord, Intervention, Outcome, Prediction
from backend.models.governance import Experiment, ModelVersion
from backend.repositories.core import PaymentEventRepository
from backend.repositories.decision import DecisionCycleRepository
from backend.repositories.governance import ExperimentRepository
from backend.schemas.audit import build_case_audit
from backend.schemas.core import PaymentEventRead, PaymentRead
from backend.schemas.dashboard import (
    ActionCounts,
    DashboardRead,
    ExecutionStatusCounts,
    ExperimentAssignmentRead,
    HighlightedCases,
    RecoveryByAction,
    RecoveryCaseDetailRead,
    RecoveryCaseListItem,
    RecoveryCaseListResponse,
)

_NOT_RECOVERED_TERMINAL = (
    enums.RecoveryCaseStatus.STOPPED.value,
    enums.RecoveryCaseStatus.EXPIRED.value,
    enums.RecoveryCaseStatus.FAILED.value,
)


def get_dashboard_summary(db: Session) -> DashboardRead:
    total_cases = db.scalar(select(func.count(RecoveryCase.id))) or 0
    recovered_cases = db.scalar(
        select(func.count(RecoveryCase.id)).where(
            RecoveryCase.status == enums.RecoveryCaseStatus.RECOVERED.value
        )
    ) or 0
    not_recovered_cases = db.scalar(
        select(func.count(RecoveryCase.id)).where(
            RecoveryCase.status.in_(_NOT_RECOVERED_TERMINAL)
        )
    ) or 0
    open_cases = total_cases - recovered_cases - not_recovered_cases

    total_amount_at_risk = db.scalar(
        select(func.coalesce(func.sum(RecoveryCase.amount_at_risk), 0))
    ) or Decimal("0")

    total_recovery_amount = db.scalar(
        select(func.coalesce(func.sum(Outcome.recovery_amount), 0)).where(
            Outcome.result == enums.OutcomeResult.RECOVERED.value
        )
    ) or Decimal("0")

    decision_cycle_count = db.scalar(select(func.count(DecisionRecord.id))) or 0

    action_counts = ActionCounts()
    for action, cnt in db.execute(
        select(DecisionRecord.final_action, func.count(DecisionRecord.id)).group_by(
            DecisionRecord.final_action
        )
    ).all():
        setattr(action_counts, action, cnt)

    policy_blocked_count = db.scalar(
        select(func.count(DecisionRecord.id)).where(
            DecisionRecord.recommended_action != DecisionRecord.final_action
        )
    ) or 0

    execution_status_summary = ExecutionStatusCounts()
    for status, cnt in db.execute(
        select(Intervention.execution_status, func.count(Intervention.id)).group_by(
            Intervention.execution_status
        )
    ).all():
        setattr(execution_status_summary, status, cnt)

    # Observed outcomes by final action — observational, not a causal/
    # uplift estimate (labelled as such in the UI).
    recovery_by_action = RecoveryByAction()
    for action, result, cnt in db.execute(
        select(DecisionRecord.final_action, Outcome.result, func.count(DecisionRecord.id))
        .join(Outcome, Outcome.decision_record_id == DecisionRecord.id)
        .group_by(DecisionRecord.final_action, Outcome.result)
    ).all():
        bucket = getattr(recovery_by_action, action, None)
        if bucket is None:
            continue
        if result == enums.OutcomeResult.RECOVERED.value:
            bucket.recovered = cnt
        else:
            bucket.not_recovered = cnt

    highlighted_cases = HighlightedCases(
        hero_recovered_case_id=_find_hero_recovered_case(db),
        policy_block_case_id=_find_policy_block_case(db),
        multi_cycle_case_id=_find_multi_cycle_case(db),
    )

    return DashboardRead(
        total_cases=total_cases,
        open_cases=open_cases,
        recovered_cases=recovered_cases,
        not_recovered_cases=not_recovered_cases,
        total_amount_at_risk=total_amount_at_risk,
        total_recovery_amount=total_recovery_amount,
        decision_cycle_count=decision_cycle_count,
        action_counts=action_counts,
        no_action_count=action_counts.NO_ACTION,
        policy_blocked_count=policy_blocked_count,
        execution_status_summary=execution_status_summary,
        recovery_by_action=recovery_by_action,
        highlighted_cases=highlighted_cases,
    )


def _promoted_model_version_id(db: Session) -> uuid.UUID | None:
    return db.scalar(
        select(ModelVersion.id).where(
            ModelVersion.model_role == "recovery_prediction",
            ModelVersion.status == enums.ModelVersionStatus.PROMOTED.value,
        )
    )


def _driven_by_promoted_model(q, promoted_mv_id: uuid.UUID):
    """Restrict a DecisionRecord query to cycles whose Predictions came
    from the currently PROMOTED model — never the bulk simulator's
    internal ``sim-naive-prior`` placeholder, which is VALIDATED but
    never PROMOTED and isn't the real live inference path."""
    return q.join(Prediction, Prediction.decision_record_id == DecisionRecord.id).where(
        Prediction.model_version_id == promoted_mv_id
    )


def _find_hero_recovered_case(db: Session) -> uuid.UUID | None:
    """A real RETRY/MESSAGE cycle, decided by the actual promoted
    T-learner, that recovered — the strongest single case for the
    dashboard's decision-pipeline illustration."""
    promoted_mv_id = _promoted_model_version_id(db)
    if promoted_mv_id is None:
        return None
    q = (
        select(DecisionRecord.recovery_case_id)
        .join(Outcome, Outcome.decision_record_id == DecisionRecord.id)
        .where(
            DecisionRecord.final_action.in_(
                [enums.Action.RETRY.value, enums.Action.MESSAGE.value]
            ),
            Outcome.result == enums.OutcomeResult.RECOVERED.value,
        )
    )
    q = _driven_by_promoted_model(q, promoted_mv_id).order_by(
        DecisionRecord.cycle_number.asc()
    ).limit(1)
    row = db.execute(q).first()
    return row[0] if row else None


def _find_policy_block_case(db: Session) -> uuid.UUID | None:
    """A real cycle, decided by the actual promoted T-learner, where the
    policy veto changed the final action."""
    promoted_mv_id = _promoted_model_version_id(db)
    if promoted_mv_id is None:
        return None
    q = select(DecisionRecord.recovery_case_id).where(
        DecisionRecord.recommended_action != DecisionRecord.final_action
    )
    q = _driven_by_promoted_model(q, promoted_mv_id).limit(1)
    row = db.execute(q).first()
    return row[0] if row else None


def _find_multi_cycle_case(db: Session) -> uuid.UUID | None:
    """A real case with 2+ immutable decision cycles, decided by the
    actual promoted T-learner."""
    promoted_mv_id = _promoted_model_version_id(db)
    if promoted_mv_id is None:
        return None
    q = (
        select(DecisionRecord.recovery_case_id)
        .join(Prediction, Prediction.decision_record_id == DecisionRecord.id)
        .where(Prediction.model_version_id == promoted_mv_id)
        .group_by(DecisionRecord.recovery_case_id)
        .having(func.count(func.distinct(DecisionRecord.id)) >= 2)
        .limit(1)
    )
    row = db.execute(q).first()
    return row[0] if row else None


def list_recovery_cases(
    db: Session, *, limit: int = 25, offset: int = 0, status: str | None = None
) -> RecoveryCaseListResponse:
    q = select(RecoveryCase)
    if status:
        q = q.where(RecoveryCase.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    cases = db.scalars(
        q.order_by(RecoveryCase.opened_at.desc()).limit(limit).offset(offset)
    ).all()

    dc_repo = DecisionCycleRepository(db)
    items: list[RecoveryCaseListItem] = []
    for case in cases:
        payment = db.get(Payment, case.payment_id)
        cycles = dc_repo.cycles_for_case(case.id)
        latest = cycles[-1] if cycles else None
        latest_outcome = latest.outcome if latest else None
        items.append(
            RecoveryCaseListItem(
                recovery_case_id=case.id,
                case_display_id=case.display_id,
                payment_id=case.payment_id,
                payment_display_id=payment.display_id if payment else None,
                payment_amount=payment.amount if payment else case.amount_at_risk,
                currency=payment.currency if payment else "INR",
                status=case.status,
                cycle_count=len(cycles),
                latest_recommended_action=latest.recommended_action if latest else None,
                latest_final_action=latest.final_action if latest else None,
                latest_outcome_result=latest_outcome.result if latest_outcome else None,
                opened_at=case.opened_at,
            )
        )
    return RecoveryCaseListResponse(items=items, total=total, limit=limit, offset=offset)


def get_recovery_case_detail(
    db: Session, case_id: uuid.UUID
) -> RecoveryCaseDetailRead | None:
    case = db.get(RecoveryCase, case_id)
    if case is None:
        return None
    base = build_case_audit(db, case)

    payment = db.get(Payment, case.payment_id)
    payment_events = PaymentEventRepository(db).list_for_payment(case.payment_id)

    experiment_assignment = None
    ea = ExperimentRepository(db).assignment_for_case(case.id)
    if ea is not None:
        exp = db.get(Experiment, ea.experiment_id)
        experiment_assignment = ExperimentAssignmentRead(
            experiment_id=ea.experiment_id,
            experiment_name=exp.name if exp else None,
            arm=ea.arm,
            assigned_at=ea.assigned_at,
        )

    return RecoveryCaseDetailRead(
        **base.model_dump(),
        payment=PaymentRead.model_validate(payment) if payment else None,
        payment_events=[PaymentEventRead.model_validate(e) for e in payment_events],
        experiment_assignment=experiment_assignment,
    )
