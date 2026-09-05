"""Resolve the one PROMOTED recovery model for the decision engine.

Phase 5. Thin wrapper over ``ml.inference.load_promoted`` that also hands
back the exact ``ModelVersion`` row (the decision engine needs both — the
predictor and the version it is bound to). Raises a clear error the API
turns into a 409 when nothing is promoted yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.models.governance import ModelVersion
from backend.repositories.governance import ModelVersionRepository
from ml.inference.recovery import RecoveryInference, load_for_model_version
from ml.training.train import MODEL_ROLE


class NoPromotedModelError(RuntimeError):
    """No PROMOTED ModelVersion for the recovery role — train + promote one
    first (``python -m ml.cli train --kind t_learner --promote``)."""


@dataclass(frozen=True)
class PromotedModel:
    predictor: RecoveryInference
    model_version: ModelVersion


def get_promoted_model(db: Session, *, model_role: str = MODEL_ROLE) -> PromotedModel:
    mv = ModelVersionRepository(db).promoted_for_role(model_role)
    if mv is None:
        raise NoPromotedModelError(
            f"no PROMOTED ModelVersion for model_role={model_role!r}"
        )
    predictor = load_for_model_version(mv)
    return PromotedModel(predictor=predictor, model_version=mv)
