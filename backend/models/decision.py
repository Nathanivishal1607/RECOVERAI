"""Phase 1A.2 decision data contract (ADR-010).

DecisionRecord, Prediction, PolicyEvaluation, Intervention, Outcome.

``Recommendation`` is a *logical* concept — ``DecisionRecord.recommended_action``
plus the value context — not a separate table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base, CreatedAtMixin, UUIDPKMixin
from backend.database.types import GUID, JSONColumn
from backend.models import enums

Money = Numeric(18, 4, asdecimal=True)
Prob = Numeric(9, 8, asdecimal=True)  # 0.00000000 .. 1.0


class DecisionRecord(UUIDPKMixin, CreatedAtMixin, Base):
    """One evaluate->decide cycle. Immutable history; re-evaluation makes a
    NEW record with a higher ``cycle_number``.

    ``model_version`` is NOT a column here — it is derived from this
    record's Predictions (all share one ModelVersion in the MVP).
    ``experiment_arm`` is NOT here either — read it via the RecoveryCase's
    ExperimentAssignment.
    """

    __tablename__ = "decision_record"

    recovery_case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("recovery_case.id"), nullable=False, index=True
    )
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payment_amount_at_decision: Mapped[float] = mapped_column(Money, nullable=False)
    # Recommendation (pre-policy) vs final (authorized) — stored SEPARATELY.
    recommended_action: Mapped[str] = mapped_column(String(16), nullable=False)
    final_action: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    # Policy identity/version evaluated this cycle (audit reconstruction).
    policy_id: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[str | None] = mapped_column(String(32))
    decision_engine_version: Mapped[str | None] = mapped_column(String(64))
    # Per-candidate economic context: [{action, cost_used, eirv_value}, ...]
    # (per-action recovery_probability lives on the Prediction rows).
    value_context: Mapped[list | None] = mapped_column(JSONColumn)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=enums.DecisionRecordStatus.DECIDED.value
    )

    predictions: Mapped[list[Prediction]] = relationship(
        back_populates="decision_record",
        order_by="Prediction.action",
        cascade="all, delete-orphan",
    )
    policy_evaluations: Mapped[list[PolicyEvaluation]] = relationship(
        back_populates="decision_record",
        order_by="PolicyEvaluation.action",
        cascade="all, delete-orphan",
    )
    intervention: Mapped[Intervention | None] = relationship(
        back_populates="decision_record",
        uselist=False,
        cascade="all, delete-orphan",
    )
    outcome: Mapped[Outcome | None] = relationship(
        back_populates="decision_record",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("recovery_case_id", "cycle_number", name="case_cycle"),
        CheckConstraint(
            recommended_action.in_(enums.values(enums.Action)),
            name="recommended_action_valid",
        ),
        CheckConstraint(
            final_action.in_(enums.values(enums.Action)),
            name="final_action_valid",
        ),
        CheckConstraint(
            status.in_(enums.values(enums.DecisionRecordStatus)), name="status_valid"
        ),
        CheckConstraint("cycle_number >= 1", name="cycle_number_positive"),
    )


class Prediction(UUIDPKMixin, CreatedAtMixin, Base):
    """A model-generated estimate for ONE candidate action in ONE cycle.

    Action-specific; bound to the EXACT immutable ModelVersion. A
    Prediction is NOT EIRV and NOT a recommendation.

    DDL table name kept as ``model_prediction`` (docs/database-schema.md).
    """

    __tablename__ = "model_prediction"

    decision_record_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("decision_record.id"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("recovery_case.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    recovery_probability: Mapped[float] = mapped_column(Prob, nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("model_version.id"), nullable=False, index=True
    )
    # Immutable inputs, as of the DecisionRecord (no post-decision data).
    feature_snapshot: Mapped[dict] = mapped_column(JSONColumn, nullable=False)

    decision_record: Mapped[DecisionRecord] = relationship(back_populates="predictions")
    model_version: Mapped["object"] = relationship("ModelVersion")

    __table_args__ = (
        UniqueConstraint("decision_record_id", "action"),
        CheckConstraint(action.in_(enums.values(enums.Action)), name="action_valid"),
        CheckConstraint(
            "recovery_probability >= 0 AND recovery_probability <= 1",
            name="probability_range",
        ),
    )


class PolicyEvaluation(UUIDPKMixin, Base):
    """Policy authorization for ONE candidate action — a distinct record,
    separate from the recommendation. One per candidate the veto loop
    checked. Retains policy id + version for historical reconstruction.
    """

    __tablename__ = "policy_evaluation"

    decision_record_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("decision_record.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(48))
    reason: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    decision_record: Mapped[DecisionRecord] = relationship(
        back_populates="policy_evaluations"
    )

    __table_args__ = (
        UniqueConstraint("decision_record_id", "action"),
        CheckConstraint(action.in_(enums.values(enums.Action)), name="action_valid"),
        CheckConstraint(
            result.in_(enums.values(enums.PolicyResult)), name="result_valid"
        ),
    )


class Intervention(UUIDPKMixin, Base):
    """An action ACTUALLY attempted/executed. Exists ONLY when the cycle's
    ``final_action`` is RETRY or MESSAGE — NEVER for NO_ACTION.

    ``execution_status`` has NO ``SUCCEEDED`` — recovery success is an
    Outcome question. Policy result is NOT stored here.
    """

    __tablename__ = "intervention"

    decision_record_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("decision_record.id"),
        nullable=False,
        unique=True,  # 0..1 per DecisionRecord
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("recovery_case.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(32))
    execution_status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    cost_incurred: Mapped[float] = mapped_column(Money, nullable=False, default=0)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    decision_record: Mapped[DecisionRecord] = relationship(back_populates="intervention")

    __table_args__ = (
        CheckConstraint(
            action.in_(list(enums.EXECUTABLE_ACTIONS)),
            name="action_executable_only",
        ),
        CheckConstraint(
            execution_status.in_(enums.values(enums.ExecutionStatus)),
            name="execution_status_valid",
        ),
        CheckConstraint("cost_incurred >= 0", name="cost_non_negative"),
    )


class Outcome(UUIDPKMixin, CreatedAtMixin, Base):
    """What happened to the PAYMENT after this decision cycle.

    Attaches to the DecisionRecord (so a NO_ACTION cycle can have one);
    optionally references the Intervention it followed. Distinct from
    ``execution_status`` and from ``RecoveryCase.status``. Delayed outcomes
    supported via ``observed_at``. Immutable once resolved.

    DDL ``recorded_at`` == this model's ``created_at``.
    """

    __tablename__ = "outcome"

    decision_record_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("decision_record.id"),
        nullable=False,
        unique=True,  # one resolved outcome per cycle
    )
    intervention_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intervention.id")
    )
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    recovery_amount: Mapped[float] = mapped_column(Money, nullable=False, default=0)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    decision_record: Mapped[DecisionRecord] = relationship(back_populates="outcome")

    __table_args__ = (
        CheckConstraint(
            result.in_(enums.values(enums.OutcomeResult)), name="result_valid"
        ),
        CheckConstraint("recovery_amount >= 0", name="recovery_amount_non_negative"),
        CheckConstraint(
            "result = 'RECOVERED' OR recovery_amount = 0",
            name="amount_zero_when_not_recovered",
        ),
    )
