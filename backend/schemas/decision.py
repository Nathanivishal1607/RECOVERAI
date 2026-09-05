"""Schemas for the Phase 1A.2 decision lifecycle (read models).

``recommended_action`` and ``final_action`` are surfaced *separately*
(they can differ). ``model_version_id`` appears on Predictions, not on the
DecisionRecord.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

_ORM = ConfigDict(from_attributes=True, protected_namespaces=())


class PredictionRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    action: str
    recovery_probability: Decimal
    model_version_id: uuid.UUID
    feature_snapshot: dict


class PolicyEvaluationRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    action: str
    policy_id: str
    policy_version: str
    result: str
    reason_code: str | None
    reason: str | None
    evaluated_at: datetime


class InterventionRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    action: str
    channel: str | None
    execution_status: str
    provider_ref: str | None
    cost_incurred: Decimal
    requested_at: datetime
    resolved_at: datetime | None


class OutcomeRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    intervention_id: uuid.UUID | None
    result: str
    recovery_amount: Decimal
    observed_at: datetime


class DecisionRecordRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    recovery_case_id: uuid.UUID
    cycle_number: int
    decision_timestamp: datetime
    payment_amount_at_decision: Decimal
    recommended_action: str
    final_action: str
    decision_reason: str | None
    policy_id: str | None
    policy_version: str | None
    value_context: list | None
    status: str
    predictions: list[PredictionRead] = []
    policy_evaluations: list[PolicyEvaluationRead] = []
    intervention: InterventionRead | None = None
    outcome: OutcomeRead | None = None
