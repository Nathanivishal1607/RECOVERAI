"""Model definitions + persisted artifacts.

Phase 3 ships one model: :class:`ml.models.recovery_model.RecoveryModel`
— a calibrated-ish logistic-regression S-learner that predicts
``P(recovery | features, action)`` for RETRY / MESSAGE / NO_ACTION.

Trained artifacts are written under ``ml/models/artifacts/`` (git-ignored)
and referenced by an immutable ``ModelVersion`` row.
"""

from ml.models.recovery_model import ARTIFACT_DIR, RecoveryModel
from ml.models.uplift import (
    ALL_KINDS,
    IncrementalModel,
    LGBMSLearnerModel,
    SLearnerModel,
    TLearnerModel,
    TreeSLearnerModel,
    build_model,
    lightgbm_available,
)

__all__ = [
    "RecoveryModel",
    "ARTIFACT_DIR",
    "IncrementalModel",
    "SLearnerModel",
    "TLearnerModel",
    "TreeSLearnerModel",
    "LGBMSLearnerModel",
    "build_model",
    "lightgbm_available",
    "ALL_KINDS",
]
