"""Data access for the Phase 1A.3 governance entities.

Contract rules enforced here (+ tested):

* ``ModelVersion``: immutable except ``status``; only the ADR-011 lifecycle
  transitions are allowed; a REJECTED version can never become PROMOTED;
  at most one PROMOTED per ``model_role``.
* ``Policy``: rule fields immutable per version; a change is a NEW version;
  at most one active version per merchant.
* ``ExperimentAssignment``: exactly one per RecoveryCase; ``arm`` immutable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.errors import (
    ExperimentAlreadyAssignedError,
    InvalidTransitionError,
    PromotedModelExistsError,
)
from backend.database.base import utcnow
from backend.models import enums
from backend.models.governance import (
    Experiment,
    ExperimentAssignment,
    ModelVersion,
    Policy,
)

_IMMUTABLE_POLICY_FIELDS = {
    "policy_id",
    "policy_version",
    "merchant_id",
    "max_retry_count",
    "max_customer_contacts",
    "contact_window_days",
    "allowed_interventions",
    "allowed_channels",
    "restricted_hours",
    "consent_required_actions",
    "minimum_amount",
    "max_autonomous_amount",
    "risk_threshold",
    "case_expiry_days",
}


class ModelVersionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        model_role: str,
        model_name: str,
        version: str,
        status: str = enums.ModelVersionStatus.DRAFT.value,
        created_at: datetime | None = None,
        **metadata,
    ) -> ModelVersion:
        if status == enums.ModelVersionStatus.PROMOTED.value:
            self._assert_no_promoted(model_role)
        mv = ModelVersion(
            model_role=model_role,
            model_name=model_name,
            version=version,
            status=status,
            created_at=created_at or utcnow(),
            status_changed_at=created_at or utcnow(),
            **{k: v for k, v in metadata.items() if hasattr(ModelVersion, k)},
        )
        self.db.add(mv)
        self.db.flush()
        return mv

    def transition_status(self, mv: ModelVersion, new_status: str) -> ModelVersion:
        allowed = enums.MODEL_VERSION_TRANSITIONS.get(mv.status, frozenset())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"ModelVersion {mv.model_role}/{mv.version}: "
                f"{mv.status} -> {new_status} is not allowed"
            )
        if new_status == enums.ModelVersionStatus.PROMOTED.value:
            self._assert_no_promoted(mv.model_role, exclude=mv.id)
        mv.status = new_status
        mv.status_changed_at = utcnow()
        self.db.flush()
        return mv

    def promoted_for_role(self, model_role: str) -> ModelVersion | None:
        return self.db.scalar(
            select(ModelVersion).where(
                ModelVersion.model_role == model_role,
                ModelVersion.status == enums.ModelVersionStatus.PROMOTED.value,
            )
        )

    def _assert_no_promoted(
        self, model_role: str, *, exclude: uuid.UUID | None = None
    ) -> None:
        existing = self.promoted_for_role(model_role)
        if existing is not None and existing.id != exclude:
            raise PromotedModelExistsError(
                f"model_role '{model_role}' already has a PROMOTED version "
                f"({existing.version}); retire it first"
            )

    def get(self, mv_id: uuid.UUID) -> ModelVersion | None:
        return self.db.get(ModelVersion, mv_id)


class PolicyRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_version(
        self,
        *,
        policy_id: str,
        policy_version: str,
        merchant_id: uuid.UUID,
        make_active: bool = True,
        **rules,
    ) -> Policy:
        """Create a new immutable policy version. If ``make_active`` the
        previously active version for the merchant is deactivated first."""
        if make_active:
            current = self.active_for_merchant(merchant_id)
            if current is not None:
                current.is_active = False
                self.db.flush()
        p = Policy(
            policy_id=policy_id,
            policy_version=policy_version,
            merchant_id=merchant_id,
            is_active=make_active,
            **{k: v for k, v in rules.items() if hasattr(Policy, k)},
        )
        self.db.add(p)
        self.db.flush()
        return p

    def active_for_merchant(self, merchant_id: uuid.UUID) -> Policy | None:
        return self.db.scalar(
            select(Policy).where(
                Policy.merchant_id == merchant_id, Policy.is_active.is_(True)
            )
        )

    def get_version(self, policy_id: str, policy_version: str) -> Policy | None:
        return self.db.scalar(
            select(Policy).where(
                Policy.policy_id == policy_id,
                Policy.policy_version == policy_version,
            )
        )

    def assert_rules_unchanged(self, policy: Policy, **candidate) -> None:
        """Guard: rule fields of an existing version must never be edited."""
        for field, value in candidate.items():
            if field in _IMMUTABLE_POLICY_FIELDS and getattr(policy, field) != value:
                from backend.core.errors import ImmutableRecordError

                raise ImmutableRecordError(
                    f"Policy {policy.policy_id}/{policy.policy_version}: "
                    f"field '{field}' is immutable — create a new version"
                )


class ExperimentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self, *, name: str, description: str | None = None,
        status: str = enums.ExperimentStatus.DRAFT.value,
    ) -> Experiment:
        exp = Experiment(name=name, description=description, status=status)
        self.db.add(exp)
        self.db.flush()
        return exp

    def assign(
        self,
        *,
        experiment_id: uuid.UUID,
        recovery_case_id: uuid.UUID,
        arm: str,
        experimental_config_ref: uuid.UUID | None = None,
        assigned_at: datetime | None = None,
    ) -> ExperimentAssignment:
        """Assign a RecoveryCase to an arm — exactly once, immutably."""
        existing = self.assignment_for_case(recovery_case_id)
        if existing is not None:
            raise ExperimentAlreadyAssignedError(
                f"RecoveryCase {recovery_case_id} is already assigned "
                f"({existing.arm}); assignment is case-level and immutable"
            )
        ea = ExperimentAssignment(
            experiment_id=experiment_id,
            recovery_case_id=recovery_case_id,
            arm=arm,
            experimental_config_ref=experimental_config_ref,
            assigned_at=assigned_at or utcnow(),
        )
        self.db.add(ea)
        self.db.flush()
        return ea

    def assignment_for_case(
        self, recovery_case_id: uuid.UUID
    ) -> ExperimentAssignment | None:
        return self.db.scalar(
            select(ExperimentAssignment).where(
                ExperimentAssignment.recovery_case_id == recovery_case_id
            )
        )

    def arm_for_case(self, recovery_case_id: uuid.UUID) -> str | None:
        ea = self.assignment_for_case(recovery_case_id)
        return ea.arm if ea else None
