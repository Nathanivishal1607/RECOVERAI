"""Thin, stable inference interface the backend decision engine calls.

Load a model *by its immutable ``ModelVersion``* (never "the latest") and
score ``P(recovery | features, action)`` for each candidate action.
"""

from ml.inference.recovery import (
    RecoveryInference,
    load_for_model_version,
    load_promoted,
)

__all__ = ["RecoveryInference", "load_for_model_version", "load_promoted"]
