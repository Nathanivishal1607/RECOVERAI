"""Phase 6 — read-only dashboard/list/detail schemas for the frontend.

These are pure *projections* over the existing Phase 1A-1B contract and the
Phase 5 audit assembly (``backend.schemas.audit``). No new persistence, no
new business semantics — just shapes convenient for a table/dashboard UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from backend.schemas.audit import CaseAuditRead
from backend.schemas.core import PaymentEventRead, PaymentRead


class ActionCounts(BaseModel):
    RETRY: int = 0
    MESSAGE: int = 0
    NO_ACTION: int = 0


class ExecutionStatusCounts(BaseModel):
    REQUESTED: int = 0
    ACCEPTED: int = 0
    REJECTED: int = 0
    FAILED: int = 0


class ActionOutcomeCounts(BaseModel):
    recovered: int = 0
    not_recovered: int = 0


class RecoveryByAction(BaseModel):
    """Observed outcomes grouped by *final* action — observational counts
    from real decision history, not a causal/uplift estimate."""

    RETRY: ActionOutcomeCounts = ActionOutcomeCounts()
    MESSAGE: ActionOutcomeCounts = ActionOutcomeCounts()
    NO_ACTION: ActionOutcomeCounts = ActionOutcomeCounts()


class HighlightedCases(BaseModel):
    """Real recovery-case ids the frontend can fetch via the existing
    ``GET /api/recovery-cases/{id}`` to render a representative decision
    story — never fabricated, just a pointer into real persisted data.
    ``None`` when the dataset has no example of that shape yet."""

    hero_recovered_case_id: uuid.UUID | None = None
    policy_block_case_id: uuid.UUID | None = None
    multi_cycle_case_id: uuid.UUID | None = None


class DashboardRead(BaseModel):
    total_cases: int
    open_cases: int
    recovered_cases: int
    not_recovered_cases: int
    total_amount_at_risk: Decimal
    total_recovery_amount: Decimal
    decision_cycle_count: int
    action_counts: ActionCounts
    no_action_count: int
    policy_blocked_count: int
    execution_status_summary: ExecutionStatusCounts
    recovery_by_action: RecoveryByAction
    highlighted_cases: HighlightedCases


class RecoveryCaseListItem(BaseModel):
    recovery_case_id: uuid.UUID
    case_display_id: str
    payment_id: uuid.UUID
    payment_display_id: str | None
    payment_amount: Decimal
    currency: str
    status: str
    cycle_count: int
    latest_recommended_action: str | None
    latest_final_action: str | None
    latest_outcome_result: str | None
    opened_at: datetime


class RecoveryCaseListResponse(BaseModel):
    items: list[RecoveryCaseListItem]
    total: int
    limit: int
    offset: int


class ExperimentAssignmentRead(BaseModel):
    experiment_id: uuid.UUID
    experiment_name: str | None
    arm: str
    assigned_at: datetime


class RecoveryCaseDetailRead(CaseAuditRead):
    payment: PaymentRead | None
    payment_events: list[PaymentEventRead]
    experiment_assignment: ExperimentAssignmentRead | None
