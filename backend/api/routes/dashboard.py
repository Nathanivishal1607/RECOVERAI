"""Phase 6 — read-only API for the frontend.

    GET /api/dashboard                              aggregate counts for Screen 1
    GET /api/recovery-cases                         paginated table for Screen 2
    GET /api/recovery-cases/{case_id}               full audit chain for Screen 3
    GET /api/recovery-cases/{case_id}/explanation   Phase 12A — LLM explanation

Read-only. Reuses the Phase 5 audit assembly (``backend.schemas.audit``)
and the existing Phase 1B repositories — no new persistence, no changes to
the Phase 5 ``/payments`` / ``/cases`` / ``/decisions`` routes. The
explanation route is a pure natural-language side channel over the same
``RecoveryCaseDetailRead`` the case-detail screen already renders — see
backend/services/explanation.py for why it can never affect the decision.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database.session import get_db
from backend.schemas.dashboard import (
    DashboardRead,
    RecoveryCaseDetailRead,
    RecoveryCaseListResponse,
)
from backend.schemas.explanation import DecisionExplanation
from backend.services import dashboard_query as dq
from backend.services import explanation as explanation_service

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardRead:
    return dq.get_dashboard_summary(db)


@router.get("/recovery-cases", response_model=RecoveryCaseListResponse)
def list_recovery_cases(
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RecoveryCaseListResponse:
    return dq.list_recovery_cases(db, limit=limit, offset=offset, status=status)


@router.get("/recovery-cases/{case_id}", response_model=RecoveryCaseDetailRead)
def get_recovery_case_detail(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> RecoveryCaseDetailRead:
    detail = dq.get_recovery_case_detail(db, case_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"RecoveryCase {case_id} not found")
    return detail


@router.get(
    "/recovery-cases/{case_id}/explanation", response_model=DecisionExplanation
)
def get_case_explanation(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> DecisionExplanation:
    """A natural-language explanation of the case's latest decision
    cycle — read-only, after the fact. Never 500s on LLM failure; the
    service degrades to ``available=False`` instead (see
    backend/services/explanation.py)."""
    detail = dq.get_recovery_case_detail(db, case_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"RecoveryCase {case_id} not found")
    if not detail.cycles:
        raise HTTPException(
            status_code=404, detail=f"RecoveryCase {case_id} has no decision cycles yet"
        )
    latest_cycle = detail.cycles[-1]
    return explanation_service.explain_decision(detail, latest_cycle)
