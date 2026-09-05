"""Phase 5 — the smallest clean API to demonstrate the end-to-end flow.

    POST /payments                      create a failed payment
    POST /payments/{id}/evaluate       eligibility + one decision cycle
    GET  /cases/{id}                   case + full audit chain
    GET  /decisions/{id}               one decision record + audit chain
    POST /decisions/{id}/execute       mock-execute the selected action
    POST /decisions/{id}/outcome       record the observed outcome
    POST /cases/{id}/reevaluate        new decision cycle on an existing case

All persistence goes through ``backend.services.recovery_flow`` and the
existing repositories. No new tables. NO real payment provider.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.session import get_db
from backend.models.core_entities import Payment, RecoveryCase
from backend.schemas.audit import (
    CaseAuditRead,
    DecisionAuditRead,
    build_case_audit,
    build_decision_audit,
)
from backend.schemas.core import PaymentRead
from backend.services.model_provider import NoPromotedModelError
from backend.services import recovery_flow as flow

router = APIRouter(tags=["recovery"])


# ------------------------------------------------------------------ payloads


class PaymentIngest(BaseModel):
    merchant_id: uuid.UUID
    customer_id: str
    amount: Decimal = Field(gt=0)
    currency: str = "INR"
    payment_method: str | None = None
    failure_category: str | None = None
    failure_code: str | None = None
    external_payment_id: str | None = None


class EvaluateRequest(BaseModel):
    policy_id: str | None = None
    policy_version: str | None = None


class EvaluateResponse(BaseModel):
    recovery_case_id: uuid.UUID
    case_display_id: str
    case_status: str
    decision_record_id: uuid.UUID
    cycle_number: int
    recommended_action: str
    final_action: str
    was_blocked: bool
    intervention_created: bool
    stopped_early: bool
    audit: DecisionAuditRead


class ExecuteRequest(BaseModel):
    force_status: str | None = Field(
        default=None,
        description="ACCEPTED (default) | REJECTED | FAILED — demo override",
    )


class OutcomeRequest(BaseModel):
    result: str = Field(description="RECOVERED | NOT_RECOVERED")
    recovery_amount: Decimal = Decimal("0")


# ------------------------------------------------------------------- helpers


def _payment_or_404(db: Session, payment_id: uuid.UUID) -> Payment:
    p = db.get(Payment, payment_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Payment {payment_id} not found")
    return p


def _case_or_404(db: Session, case_id: uuid.UUID) -> RecoveryCase:
    c = db.get(RecoveryCase, case_id)
    if c is None:
        raise HTTPException(status_code=404, detail=f"RecoveryCase {case_id} not found")
    return c


def _resolve_policy(db: Session, req_policy_id, req_policy_version):
    if req_policy_id is None:
        return None
    from backend.repositories.governance import PolicyRepository

    pol = PolicyRepository(db).get_version(req_policy_id, req_policy_version or "")
    if pol is None:
        raise HTTPException(
            status_code=404,
            detail=f"Policy {req_policy_id}/{req_policy_version} not found",
        )
    return pol


def _no_promoted_model_409(exc: NoPromotedModelError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"{exc} — run `python -m ml.cli train --kind t_learner --promote`",
    )


# -------------------------------------------------------------------- routes


@router.post("/payments", response_model=PaymentRead, status_code=201)
def create_payment(body: PaymentIngest, db: Session = Depends(get_db)) -> PaymentRead:
    payment = flow.ingest_failed_payment(
        db,
        merchant_id=body.merchant_id,
        customer_id=body.customer_id,
        amount=body.amount,
        currency=body.currency,
        payment_method=body.payment_method,
        failure_category=body.failure_category,
        failure_code=body.failure_code,
        external_payment_id=body.external_payment_id,
    )
    db.commit()
    return PaymentRead.model_validate(payment)


@router.post("/payments/{payment_id}/evaluate", response_model=EvaluateResponse)
def evaluate_payment(
    payment_id: uuid.UUID,
    body: EvaluateRequest = EvaluateRequest(),
    db: Session = Depends(get_db),
) -> EvaluateResponse:
    payment = _payment_or_404(db, payment_id)
    policy = _resolve_policy(db, body.policy_id, body.policy_version)
    try:
        # promoted=None (default): the service resolves it, per-case —
        # a TREATMENT-assigned case with an experimental_config_ref uses
        # that model, everything else falls back to the PROMOTED default.
        res = flow.evaluate_recovery(db, payment=payment, policy=policy)
    except NoPromotedModelError as exc:
        db.rollback()
        raise _no_promoted_model_409(exc)
    except flow.RecoveryFlowError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    audit = build_decision_audit(db, res.decision.decision_record)
    db.commit()
    return EvaluateResponse(
        recovery_case_id=res.case.id,
        case_display_id=res.case.display_id,
        case_status=res.case_status,
        decision_record_id=res.decision.decision_record.id,
        cycle_number=res.cycle_number,
        recommended_action=res.decision.recommended_action,
        final_action=res.decision.final_action,
        was_blocked=res.decision.recommended_action != res.decision.final_action,
        intervention_created=res.decision.intervention_created,
        stopped_early=res.stopped_early,
        audit=audit,
    )


@router.get("/cases/{case_id}", response_model=CaseAuditRead)
def get_case(case_id: uuid.UUID, db: Session = Depends(get_db)) -> CaseAuditRead:
    case = _case_or_404(db, case_id)
    return build_case_audit(db, case)


@router.get("/decisions/{decision_record_id}", response_model=DecisionAuditRead)
def get_decision(
    decision_record_id: uuid.UUID, db: Session = Depends(get_db)
) -> DecisionAuditRead:
    from backend.repositories.decision import DecisionCycleRepository

    dr = DecisionCycleRepository(db).get(decision_record_id)
    if dr is None:
        raise HTTPException(
            status_code=404, detail=f"DecisionRecord {decision_record_id} not found"
        )
    return build_decision_audit(db, dr)


@router.post("/decisions/{decision_record_id}/execute", response_model=DecisionAuditRead)
def execute_decision(
    decision_record_id: uuid.UUID,
    body: ExecuteRequest = ExecuteRequest(),
    db: Session = Depends(get_db),
) -> DecisionAuditRead:
    try:
        flow.execute_decision(
            db, decision_record_id=decision_record_id, force_status=body.force_status
        )
    except flow.RecoveryFlowError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    from backend.repositories.decision import DecisionCycleRepository

    dr = DecisionCycleRepository(db).get(decision_record_id)
    audit = build_decision_audit(db, dr)
    db.commit()
    return audit


@router.post("/decisions/{decision_record_id}/outcome", response_model=DecisionAuditRead)
def record_outcome(
    decision_record_id: uuid.UUID,
    body: OutcomeRequest,
    db: Session = Depends(get_db),
) -> DecisionAuditRead:
    try:
        dr = flow.record_outcome(
            db,
            decision_record_id=decision_record_id,
            result=body.result,
            recovery_amount=body.recovery_amount,
        )
    except flow.RecoveryFlowError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    audit = build_decision_audit(db, dr)
    db.commit()
    return audit


@router.post("/cases/{case_id}/reevaluate", response_model=EvaluateResponse)
def reevaluate_case(
    case_id: uuid.UUID,
    body: EvaluateRequest = EvaluateRequest(),
    db: Session = Depends(get_db),
) -> EvaluateResponse:
    case = _case_or_404(db, case_id)
    policy = _resolve_policy(db, body.policy_id, body.policy_version)
    try:
        # promoted=None (default): resolved per-case by the service, same
        # as evaluate_payment — preserves the case's experiment arm across
        # cycles automatically, since the assignment itself never changes.
        res = flow.reevaluate(db, case=case, policy=policy)
    except NoPromotedModelError as exc:
        db.rollback()
        raise _no_promoted_model_409(exc)
    except flow.RecoveryFlowError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    audit = build_decision_audit(db, res.decision.decision_record)
    db.commit()
    return EvaluateResponse(
        recovery_case_id=res.case.id,
        case_display_id=res.case.display_id,
        case_status=res.case_status,
        decision_record_id=res.decision.decision_record.id,
        cycle_number=res.cycle_number,
        recommended_action=res.decision.recommended_action,
        final_action=res.decision.final_action,
        was_blocked=res.decision.recommended_action != res.decision.final_action,
        intervention_created=res.decision.intervention_created,
        stopped_early=res.stopped_early,
        audit=audit,
    )
