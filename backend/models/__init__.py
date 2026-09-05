"""SQLAlchemy ORM models — the Phase 1B realization of the finalized
Phase 1A data contract.

Import this package for its side effect of registering every model on
``Base.metadata`` (used by Alembic and by ``Base.metadata.create_all``).
"""

from backend.database.base import Base
from backend.models.core_entities import (
    Customer,
    DisplayIdSequence,
    Merchant,
    Payment,
    PaymentEvent,
    RecoveryCase,
    RecoveryCaseStatusHistory,
)
from backend.models.decision import (
    DecisionRecord,
    Intervention,
    Outcome,
    PolicyEvaluation,
    Prediction,
)
from backend.models.governance import (
    Experiment,
    ExperimentAssignment,
    ModelVersion,
    Policy,
)
from backend.models.training import TrainingExample

__all__ = [
    "Base",
    "DisplayIdSequence",
    # core
    "Merchant",
    "Customer",
    "Payment",
    "PaymentEvent",
    "RecoveryCase",
    "RecoveryCaseStatusHistory",
    # governance
    "ModelVersion",
    "Policy",
    "Experiment",
    "ExperimentAssignment",
    # decision
    "DecisionRecord",
    "Prediction",
    "PolicyEvaluation",
    "Intervention",
    "Outcome",
    # training
    "TrainingExample",
]
