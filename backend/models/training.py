"""Phase 1A.4 training data contract (ADR-012).

``TrainingExample`` — a DERIVED, IMMUTABLE ML observation.
Logical unit: one row per (DecisionRecord x candidate action).

Only the ``observed_action`` row carries an ``outcome_label`` — the other
candidates stay predictions, never manufactured counterfactual labels.
``recovery_case_id`` is the grouping key for CASE-LEVEL train/val/test
splitting.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base, CreatedAtMixin, UUIDPKMixin
from backend.database.types import GUID, JSONColumn
from backend.models import enums

Money = Numeric(18, 4, asdecimal=True)


class TrainingExample(UUIDPKMixin, CreatedAtMixin, Base):
    __tablename__ = "training_example"

    decision_record_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("decision_record.id"), nullable=False, index=True
    )
    # GROUPING KEY — all rows of a case go in ONE train/val/test split.
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("recovery_case.id"), nullable=False, index=True
    )
    # The candidate action this row is about (the treatment feature).
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    # What actually happened this cycle (derived, NOT the recommendation).
    observed_action: Mapped[str] = mapped_column(String(16), nullable=False)
    # True iff action == observed_action AND the outcome is usable.
    is_observed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Features AS OF the DecisionRecord — no post-decision data (no leakage).
    feature_snapshot: Mapped[dict] = mapped_column(JSONColumn, nullable=False)
    # Label — ONLY when is_observed; else NULL (no counterfactual labels).
    outcome_label: Mapped[str | None] = mapped_column(String(16))
    recovery_amount: Mapped[float | None] = mapped_column(Money)
    observation_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Inherited from the RecoveryCase's ExperimentAssignment (case-level).
    experiment_arm: Mapped[str | None] = mapped_column(String(16))
    # Via the cycle's Predictions.
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("model_version.id"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("decision_record_id", "action"),
        CheckConstraint(action.in_(enums.values(enums.Action)), name="action_valid"),
        CheckConstraint(
            observed_action.in_(enums.values(enums.Action)),
            name="observed_action_valid",
        ),
        CheckConstraint(
            "outcome_label IS NULL OR outcome_label IN ('RECOVERED','NOT_RECOVERED')",
            name="outcome_label_valid",
        ),
        # A label may exist ONLY on an observed row (no counterfactual labels).
        CheckConstraint(
            "outcome_label IS NULL OR is_observed",
            name="label_only_when_observed",
        ),
        # An observed row's action must be the action actually observed.
        CheckConstraint(
            "NOT is_observed OR action = observed_action",
            name="observed_row_action_matches",
        ),
        CheckConstraint(
            "experiment_arm IS NULL OR experiment_arm IN ('CONTROL','TREATMENT')",
            name="experiment_arm_valid",
        ),
    )
