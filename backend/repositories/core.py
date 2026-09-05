"""Data access for the Phase 1A.1 core entities.

Repositories are use-case shaped, not blanket CRUD. Contract rules the DB
can't fully express are enforced here and re-tested in tests/backend:

* ``PaymentEvent`` is append-only — there is no update/delete.
* ``RecoveryCase``: at most one ACTIVE case per payment; status changes go
  through :meth:`RecoveryCaseRepository.transition` which also appends an
  immutable status-history row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.errors import ActiveCaseExistsError, InvalidTransitionError
from backend.database.base import utcnow
from backend.models import enums
from backend.models.core_entities import (
    Customer,
    Merchant,
    Payment,
    PaymentEvent,
    RecoveryCase,
    RecoveryCaseStatusHistory,
)
from backend.repositories.identifiers import next_display_id


class MerchantRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        status: str = enums.MerchantStatus.ACTIVE.value,
        industry: str | None = None,
        currency: str = "INR",
    ) -> Merchant:
        m = Merchant(
            display_id=next_display_id(self.db, "merchant"),
            name=name,
            status=status,
            industry=industry,
            currency=currency,
        )
        self.db.add(m)
        self.db.flush()
        return m

    def get(self, merchant_id: uuid.UUID) -> Merchant | None:
        return self.db.get(Merchant, merchant_id)

    def get_by_display_id(self, display_id: str) -> Merchant | None:
        return self.db.scalar(
            select(Merchant).where(Merchant.display_id == display_id)
        )


class CustomerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, customer_id: str, merchant_id: uuid.UUID, **kwargs) -> Customer:
        c = Customer(customer_id=customer_id, merchant_id=merchant_id, **kwargs)
        self.db.add(c)
        self.db.flush()
        return c

    def get(self, customer_id: str) -> Customer | None:
        return self.db.get(Customer, customer_id)


class PaymentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        merchant_id: uuid.UUID,
        customer_id: str,
        amount,
        currency: str,
        status: str = enums.PaymentStatus.CREATED.value,
        external_payment_id: str | None = None,
        payment_method: str | None = None,
        payment_method_type: str | None = None,
    ) -> Payment:
        p = Payment(
            display_id=next_display_id(self.db, "payment"),
            merchant_id=merchant_id,
            customer_id=customer_id,
            amount=amount,
            currency=currency,
            status=status,
            external_payment_id=external_payment_id,
            payment_method=payment_method,
            payment_method_type=payment_method_type,
        )
        self.db.add(p)
        self.db.flush()
        return p

    def get(self, payment_id: uuid.UUID) -> Payment | None:
        return self.db.get(Payment, payment_id)

    def set_status(self, payment: Payment, status: str) -> Payment:
        """Update the denormalized status. The authoritative history is the
        ordered ``payment_event`` stream (:class:`PaymentEventRepository`)."""
        payment.status = status
        self.db.flush()
        return payment


class PaymentEventRepository:
    """Append-only. No ``update`` / ``delete`` — a correction is a new event."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def append(
        self,
        *,
        payment_id: uuid.UUID,
        event_type: str,
        event_timestamp: datetime,
        attempt_number: int | None = None,
        amount=None,
        currency: str | None = None,
        provider_event_id: str | None = None,
        metadata: dict | None = None,
    ) -> PaymentEvent:
        evt = PaymentEvent(
            payment_id=payment_id,
            event_type=event_type,
            event_timestamp=event_timestamp,
            attempt_number=attempt_number,
            amount=amount,
            currency=currency,
            provider_event_id=provider_event_id,
            event_metadata=metadata,
        )
        self.db.add(evt)
        self.db.flush()
        return evt

    def list_for_payment(self, payment_id: uuid.UUID) -> list[PaymentEvent]:
        return list(
            self.db.scalars(
                select(PaymentEvent)
                .where(PaymentEvent.payment_id == payment_id)
                .order_by(PaymentEvent.event_timestamp, PaymentEvent.created_at)
            )
        )


class RecoveryCaseRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def active_for_payment(self, payment_id: uuid.UUID) -> RecoveryCase | None:
        return self.db.scalar(
            select(RecoveryCase).where(
                RecoveryCase.payment_id == payment_id,
                RecoveryCase.status.notin_(list(enums.TERMINAL_CASE_STATUSES)),
            )
        )

    def open_case(
        self,
        *,
        payment: Payment,
        amount_at_risk,
        failure_category: str | None = None,
        failure_code: str | None = None,
        recovery_window_days: int = 14,
        opened_at: datetime | None = None,
    ) -> RecoveryCase:
        """Open a case for a failed payment. Rejects if an active case
        already exists (Phase 1A.1 business rule)."""
        if self.active_for_payment(payment.id) is not None:
            raise ActiveCaseExistsError(
                f"payment {payment.id} already has an active RecoveryCase"
            )
        opened = opened_at or utcnow()
        case = RecoveryCase(
            display_id=next_display_id(self.db, "recovery_case"),
            payment_id=payment.id,
            merchant_id=payment.merchant_id,
            customer_id=payment.customer_id,
            status=enums.RecoveryCaseStatus.OPEN.value,
            opened_at=opened,
            expires_at=opened + timedelta(days=recovery_window_days),
            amount_at_risk=amount_at_risk,
            failure_category=failure_category,
            failure_code=failure_code,
        )
        self.db.add(case)
        self.db.flush()
        self._history(case, None, case.status, "case opened", opened)
        return case

    #: Valid state-machine edges (data/data-model.md, ADR-009).
    _EDGES: dict[str, set[str]] = {
        "OPEN": {"ANALYZING", "FAILED"},
        "ANALYZING": {"ACTION_SELECTED", "STOPPED", "FAILED"},
        "ACTION_SELECTED": {"ACTION_EXECUTED", "FAILED"},
        "ACTION_EXECUTED": {"WAITING_FOR_OUTCOME", "FAILED"},
        "WAITING_FOR_OUTCOME": {"RECOVERED", "ANALYZING", "STOPPED", "EXPIRED"},
        "RECOVERED": set(),
        "STOPPED": set(),
        "EXPIRED": set(),
        "FAILED": set(),
    }

    def transition(
        self,
        case: RecoveryCase,
        to_status: str,
        *,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> RecoveryCase:
        frm = case.status
        if to_status not in self._EDGES.get(frm, set()):
            raise InvalidTransitionError(
                f"RecoveryCase {case.display_id}: {frm} -> {to_status} is not allowed"
            )
        when = occurred_at or utcnow()
        case.status = to_status
        if to_status == enums.RecoveryCaseStatus.ANALYZING.value:
            case.last_evaluated_at = when
        if to_status in enums.TERMINAL_CASE_STATUSES:
            case.closed_at = when
        self.db.flush()
        self._history(case, frm, to_status, reason, when)
        return case

    def _history(
        self,
        case: RecoveryCase,
        frm: str | None,
        to: str,
        reason: str | None,
        when: datetime,
    ) -> None:
        self.db.add(
            RecoveryCaseStatusHistory(
                case_id=case.id,
                from_status=frm,
                to_status=to,
                reason=reason,
                occurred_at=when,
            )
        )
        self.db.flush()

    def get(self, case_id: uuid.UUID) -> RecoveryCase | None:
        return self.db.get(RecoveryCase, case_id)
