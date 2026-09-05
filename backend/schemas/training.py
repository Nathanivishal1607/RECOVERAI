"""Schema for the Phase 1A.4 TrainingExample (read model)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TrainingExampleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    decision_record_id: uuid.UUID
    recovery_case_id: uuid.UUID  # grouping key for case-level splitting
    action: str
    observed_action: str
    is_observed: bool
    feature_snapshot: dict
    outcome_label: str | None  # only for the observed action
    recovery_amount: Decimal | None
    observation_timestamp: datetime | None
    experiment_arm: str | None
    model_version_id: uuid.UUID
