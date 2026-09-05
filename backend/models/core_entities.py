"""Phase 1A.1 core data contract (ADR-009).

Merchant, Customer, Payment, PaymentEvent, RecoveryCase (+ its
append-only status history).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import (
    Base,
    CreatedAtMixin,
    DisplayIdMixin,
    TimestampMixin,
    UUIDPKMixin,
)
from backend.database.types import GUID, JSONColumn
from backend.models import enums

# Money: exact decimal everywhere. Postgres NUMERIC(18, 4); on SQLite this
# still returns Decimal (asdecimal default True).
Money = Numeric(18, 4, asdecimal=True)


class DisplayIdSequence(Base):
    """Per-prefix counter backing human-readable ``display_id`` generation
    (see backend/repositories/identifiers.py). Not a business entity."""

    __tablename__ = "display_id_sequence"

    prefix: Mapped[str] = mapped_column(String(16), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Merchant(UUIDPKMixin, DisplayIdMixin, TimestampMixin, Base):
    __tablename__ = "merchant"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=enums.MerchantStatus.ACTIVE.value
    )
    industry: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    __table_args__ = (
        CheckConstraint(
            status.in_(enums.values(enums.MerchantStatus)),
            name="status_valid",
        ),
    )


class Customer(CreatedAtMixin, Base):
    """Deliberately holds NO name / phone / email / card / bank fields.

    See docs/architecture/privacy-architecture.md — identity-bearing PII
    lives in a separate, tightly access-controlled store if ever needed.
    """

    __tablename__ = "customer"

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("merchant.id"), nullable=False, index=True
    )
    transaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_transactions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    failed_transactions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_transaction_value: Mapped[float | None] = mapped_column(Money)
    historical_recovery_rate: Mapped[float | None] = mapped_column(Numeric(6, 4))
    preferred_language: Mapped[str | None] = mapped_column(String(16))
    preferred_channel: Mapped[str | None] = mapped_column(String(32))
    consent_voice: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_payment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(UUIDPKMixin, DisplayIdMixin, TimestampMixin, Base):
    __tablename__ = "payment"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("merchant.id"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("customer.customer_id"), nullable=False, index=True
    )
    # Provider identifier — NOT the primary key; may be NULL for synthetic.
    external_payment_id: Mapped[str | None] = mapped_column(String(128), index=True)
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(32))
    payment_method_type: Mapped[str | None] = mapped_column(String(32))
    # Lean internal status — provider states are mapped in, never raw.
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    events: Mapped[list[PaymentEvent]] = relationship(
        back_populates="payment",
        order_by="PaymentEvent.event_timestamp, PaymentEvent.created_at",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            status.in_(enums.values(enums.PaymentStatus)),
            name="status_valid",
        ),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )


class PaymentEvent(UUIDPKMixin, CreatedAtMixin, Base):
    """Authoritative, immutable, append-only payment-lifecycle record.

    ``event_timestamp`` = when it happened (provider/world time).
    ``created_at``       = when our system ingested it (may lag).
    Never UPDATE/DELETE — a correction is a new event.
    """

    __tablename__ = "payment_event"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("payment.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Nullable: set for attempt-scoped events, NULL otherwise (Phase 1A.1).
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    provider_event_id: Mapped[str | None] = mapped_column(String(128), index=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONColumn)

    payment: Mapped[Payment] = relationship(back_populates="events")

    __table_args__ = (
        CheckConstraint(
            event_type.in_(enums.values(enums.PaymentEventType)),
            name="event_type_valid",
        ),
        CheckConstraint(
            "attempt_number IS NULL OR attempt_number >= 1",
            name="attempt_number_positive",
        ),
    )


class RecoveryCase(UUIDPKMixin, DisplayIdMixin, TimestampMixin, Base):
    """The central business/audit object. State machine per ADR-009.

    ``experiment_arm`` is deliberately NOT a column — assignment lives on
    ``experiment_assignment`` (case-level, ADR-011).
    """

    __tablename__ = "recovery_case"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("payment.id"), nullable=False, index=True
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("merchant.id"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("customer.customer_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=enums.RecoveryCaseStatus.OPEN.value
    )
    # Business-lifecycle timestamps (distinct from created_at/updated_at).
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Case context.
    amount_at_risk: Mapped[float] = mapped_column(Money, nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(32))
    failure_code: Mapped[str | None] = mapped_column(String(64))

    status_history: Mapped[list[RecoveryCaseStatusHistory]] = relationship(
        back_populates="case",
        order_by="RecoveryCaseStatusHistory.occurred_at, RecoveryCaseStatusHistory.id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            status.in_(enums.values(enums.RecoveryCaseStatus)),
            name="status_valid",
        ),
        # "At most one ACTIVE RecoveryCase per Payment" (Phase 1A.1) — a
        # partial unique index, NOT a permanent UNIQUE(payment_id): a later
        # (post-MVP) recovery episode can open once the first has closed.
        Index(
            "uq_recovery_case_active_payment",
            "payment_id",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('RECOVERED','STOPPED','EXPIRED','FAILED')"
            ),
            sqlite_where=text(
                "status NOT IN ('RECOVERED','STOPPED','EXPIRED','FAILED')"
            ),
        ),
    )


class RecoveryCaseStatusHistory(Base):
    __tablename__ = "recovery_case_status_history"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("recovery_case.id"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    case: Mapped[RecoveryCase] = relationship(back_populates="status_history")
