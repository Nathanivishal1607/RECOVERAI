"""Phase 5 — decision explanation / audit view.

Read-only projections that assemble one decision cycle (and a case's full
cycle history) into a single response answering the demo audit questions:

* what actions were considered / predicted / valued
* which action was the economic recommendation
* which policy rules were evaluated, and was the recommendation blocked
* what was the final authorized action
* was an intervention executed, and its execution status
* what was the eventual outcome
* which ModelVersion produced the predictions
* which decision cycle this is, and what happened in previous cycles

Composed from the existing ORM rows — no new persistence, no duplicate
data model. ``model_version`` is *derived* from the cycle's Predictions
(ADR-010: a DecisionRecord has no model-version column).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.models.decision import DecisionRecord
from backend.models.governance import ModelVersion

_ORM = ConfigDict(from_attributes=True, protected_namespaces=())


class ActionConsideration(BaseModel):
    action: str
    recovery_probability: float | None
    incremental_probability: float | None  # P(a) - P(NO_ACTION), derived
    eirv_value: float | None
    cost_used: float | None
    policy_result: str | None  # ALLOWED / BLOCKED / None (not checked)
    policy_reason_code: str | None
    is_recommended: bool
    is_final: bool


class ModelVersionRef(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    model_role: str
    model_name: str
    version: str
    algorithm: str | None
    status: str
    feature_schema_id: str | None
    training_dataset_snapshot_id: str | None


class CycleSummary(BaseModel):
    cycle_number: int
    decision_timestamp: datetime
    recommended_action: str
    final_action: str
    was_blocked: bool
    intervention_action: str | None
    execution_status: str | None
    outcome_result: str | None
    recovery_amount: Decimal | None


class DecisionAuditRead(BaseModel):
    """One decision cycle, fully explained."""

    decision_record_id: uuid.UUID
    recovery_case_id: uuid.UUID
    cycle_number: int
    decision_timestamp: datetime
    payment_amount_at_decision: Decimal
    status: str

    actions_considered: list[ActionConsideration]
    recommended_action: str
    final_action: str
    was_blocked: bool
    block_reason_codes: list[str]
    decision_reason: str | None

    policy_id: str | None
    policy_version: str | None
    decision_engine_version: str | None

    intervention_action: str | None
    intervention_channel: str | None
    execution_status: str | None
    intervention_cost: Decimal | None

    outcome_result: str | None
    outcome_recovery_amount: Decimal | None
    outcome_observed_at: datetime | None

    model_version: ModelVersionRef | None

    previous_cycles: list[CycleSummary]


class CaseAuditRead(BaseModel):
    model_config = _ORM
    recovery_case_id: uuid.UUID
    case_display_id: str
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    status: str
    amount_at_risk: Decimal
    failure_category: str | None
    opened_at: datetime
    closed_at: datetime | None
    cycles: list[DecisionAuditRead]


# --------------------------------------------------------------- assembly


def _cycle_summary(dr: DecisionRecord) -> CycleSummary:
    intv = dr.intervention
    oc = dr.outcome
    return CycleSummary(
        cycle_number=dr.cycle_number,
        decision_timestamp=dr.decision_timestamp,
        recommended_action=dr.recommended_action,
        final_action=dr.final_action,
        was_blocked=dr.recommended_action != dr.final_action,
        intervention_action=intv.action if intv else None,
        execution_status=intv.execution_status if intv else None,
        outcome_result=oc.result if oc else None,
        recovery_amount=oc.recovery_amount if oc else None,
    )


def build_decision_audit(
    db,
    dr: DecisionRecord,
    *,
    all_case_cycles: list[DecisionRecord] | None = None,
) -> DecisionAuditRead:
    preds = {p.action: p for p in dr.predictions}
    pol_evals = {pe.action: pe for pe in dr.policy_evaluations}
    vc = {row["action"]: row for row in (dr.value_context or [])}

    base_prob = None
    if "NO_ACTION" in preds:
        base_prob = float(preds["NO_ACTION"].recovery_probability)

    considered: list[ActionConsideration] = []
    for action in ("RETRY", "MESSAGE", "NO_ACTION"):
        p = preds.get(action)
        prob = float(p.recovery_probability) if p is not None else None
        incr = None
        if prob is not None and base_prob is not None:
            incr = round(prob - base_prob, 6)
        row = vc.get(action, {})
        pe = pol_evals.get(action)
        considered.append(
            ActionConsideration(
                action=action,
                recovery_probability=prob,
                incremental_probability=(0.0 if action == "NO_ACTION" else incr),
                eirv_value=row.get("eirv_value"),
                cost_used=row.get("cost_used"),
                policy_result=pe.result if pe else None,
                policy_reason_code=pe.reason_code if pe else None,
                is_recommended=action == dr.recommended_action,
                is_final=action == dr.final_action,
            )
        )

    block_codes = [
        pe.reason_code
        for pe in dr.policy_evaluations
        if pe.result == "BLOCKED" and pe.reason_code
    ]

    mv_ref = None
    if preds:
        any_pred = next(iter(preds.values()))
        mv = db.get(ModelVersion, any_pred.model_version_id)
        if mv is not None:
            mv_ref = ModelVersionRef.model_validate(mv)

    cycles = all_case_cycles
    if cycles is None:
        from backend.repositories.decision import DecisionCycleRepository

        cycles = DecisionCycleRepository(db).cycles_for_case(dr.recovery_case_id)
    previous = [
        _cycle_summary(c) for c in cycles if c.cycle_number < dr.cycle_number
    ]

    intv = dr.intervention
    oc = dr.outcome
    return DecisionAuditRead(
        decision_record_id=dr.id,
        recovery_case_id=dr.recovery_case_id,
        cycle_number=dr.cycle_number,
        decision_timestamp=dr.decision_timestamp,
        payment_amount_at_decision=dr.payment_amount_at_decision,
        status=dr.status,
        actions_considered=considered,
        recommended_action=dr.recommended_action,
        final_action=dr.final_action,
        was_blocked=dr.recommended_action != dr.final_action,
        block_reason_codes=block_codes,
        decision_reason=dr.decision_reason,
        policy_id=dr.policy_id,
        policy_version=dr.policy_version,
        decision_engine_version=dr.decision_engine_version,
        intervention_action=intv.action if intv else None,
        intervention_channel=intv.channel if intv else None,
        execution_status=intv.execution_status if intv else None,
        intervention_cost=intv.cost_incurred if intv else None,
        outcome_result=oc.result if oc else None,
        outcome_recovery_amount=oc.recovery_amount if oc else None,
        outcome_observed_at=oc.observed_at if oc else None,
        model_version=mv_ref,
        previous_cycles=previous,
    )


def build_case_audit(db, case) -> CaseAuditRead:
    from backend.repositories.decision import DecisionCycleRepository

    cycles = DecisionCycleRepository(db).cycles_for_case(case.id)
    return CaseAuditRead(
        recovery_case_id=case.id,
        case_display_id=case.display_id,
        payment_id=case.payment_id,
        merchant_id=case.merchant_id,
        status=case.status,
        amount_at_risk=case.amount_at_risk,
        failure_category=case.failure_category,
        opened_at=case.opened_at,
        closed_at=case.closed_at,
        cycles=[
            build_decision_audit(db, dr, all_case_cycles=cycles) for dr in cycles
        ],
    )
