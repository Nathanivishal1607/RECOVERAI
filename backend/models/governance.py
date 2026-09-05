"""Phase 1A.3 model / policy / experiment data contract (ADR-011)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base, CreatedAtMixin, UUIDPKMixin
from backend.database.types import GUID, JSONColumn
from backend.models import enums

Money = Numeric(18, 4, asdecimal=True)


class ModelVersion(UUIDPKMixin, Base):
    """One exact, reproducible model. Immutable except ``status``
    (Phase 1A.3 / ADR-011). Exactly one ``PROMOTED`` per ``model_role``.
    """

    __tablename__ = "model_version"

    model_role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str | None] = mapped_column(String(64))
    # Reproducibility metadata (all immutable).
    artifact_ref: Mapped[str | None] = mapped_column(String(512))
    artifact_checksum: Mapped[str | None] = mapped_column(String(128))
    training_dataset_snapshot_id: Mapped[str | None] = mapped_column(String(128))
    feature_schema_id: Mapped[str | None] = mapped_column(String(128))
    training_config: Mapped[dict | None] = mapped_column(JSONColumn)
    training_pipeline_version: Mapped[str | None] = mapped_column(String(64))
    random_seed: Mapped[int | None] = mapped_column(Integer)
    evaluation_summary: Mapped[dict | None] = mapped_column(JSONColumn)
    # The ONLY mutable field.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=enums.ModelVersionStatus.DRAFT.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("model_role", "model_name", "version", name="role_name_version"),
        CheckConstraint(
            status.in_(enums.values(enums.ModelVersionStatus)),
            name="status_valid",
        ),
        # Exactly one PROMOTED per model_role (Phase 1A.3).
        Index(
            "uq_model_version_promoted_per_role",
            "model_role",
            unique=True,
            postgresql_where=text("status = 'PROMOTED'"),
            sqlite_where=text("status = 'PROMOTED'"),
        ),
    )


class Policy(UUIDPKMixin, CreatedAtMixin, Base):
    """One immutable, versioned set of merchant recovery rules
    (Phase 1A.3). Rule fields never change; a change is a NEW row.

    This is POLICY DATA (what is allowed). The POLICY ENGINE
    (docs/decision-engine/policy-engine.md) is the fixed code that
    evaluates it — no executable per-merchant policy code.
    """

    __tablename__ = "policy"

    # ``policy_id`` = the policy "slot"; (policy_id, policy_version) unique.
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("merchant.id"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    # Rule categories (MVP-practical shape; arrays stored as JSON for
    # portability — a documented Phase 1B choice, contract unchanged).
    max_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    max_customer_contacts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2
    )
    contact_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    allowed_interventions: Mapped[list] = mapped_column(
        JSONColumn, nullable=False, default=lambda: ["RETRY", "MESSAGE"]
    )
    allowed_channels: Mapped[list | None] = mapped_column(JSONColumn)
    restricted_hours: Mapped[dict | None] = mapped_column(JSONColumn)
    consent_required_actions: Mapped[list | None] = mapped_column(JSONColumn)
    minimum_amount: Mapped[float | None] = mapped_column(Money)
    max_autonomous_amount: Mapped[float | None] = mapped_column(Money)
    risk_threshold: Mapped[float | None] = mapped_column(Numeric(6, 4))
    case_expiry_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)

    __table_args__ = (
        UniqueConstraint("policy_id", "policy_version", name="slot_version"),
        # At most one active policy version per merchant.
        Index(
            "uq_policy_active_per_merchant",
            "merchant_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )


class Experiment(UUIDPKMixin, CreatedAtMixin, Base):
    """An experiment definition (what is being compared). Phase 1A.3."""

    __tablename__ = "experiment"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=enums.ExperimentStatus.DRAFT.value
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assignments: Mapped[list[ExperimentAssignment]] = relationship(
        back_populates="experiment"
    )

    __table_args__ = (
        CheckConstraint(
            status.in_(enums.values(enums.ExperimentStatus)), name="status_valid"
        ),
    )


class ExperimentAssignment(UUIDPKMixin, Base):
    """CONTROL / TREATMENT for one RecoveryCase.

    CASE-LEVEL ONLY — one per ``recovery_case_id`` (unique), immutable once
    written. Every DecisionRecord under the case inherits this arm; there
    is NO per-DecisionRecord assignment (Phase 1A.3 / ADR-011).
    """

    __tablename__ = "experiment_assignment"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("experiment.id"), nullable=False, index=True
    )
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("recovery_case.id"),
        nullable=False,
        unique=True,  # one assignment per case
    )
    arm: Mapped[str] = mapped_column(String(16), nullable=False)
    # Reference (not a copy) to e.g. a ModelVersion for a model experiment.
    experimental_config_ref: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("model_version.id")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    experiment: Mapped[Experiment] = relationship(back_populates="assignments")

    __table_args__ = (
        CheckConstraint(
            arm.in_(enums.values(enums.ExperimentArm)), name="arm_valid"
        ),
    )
