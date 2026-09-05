"""Load a trained model from its immutable ``ModelVersion`` and expose a
minimal, stable scoring API for the decision engine.

* ``load_for_model_version(mv)`` — reconstruct the exact historical model
  from ``mv.artifact_ref`` and verify ``mv.artifact_checksum`` (so a
  historical decision is reproduced by the exact artifact, never by
  "the current" model). Works for every Phase 3/4 model kind
  (S-learner, T-learner, tree-S-learner, LightGBM-S-learner) via the
  kind-tagged artifact payload.
* ``load_promoted(db)`` — convenience: the one PROMOTED ModelVersion for
  ``model_role="recovery_prediction"``.

The returned object only predicts probabilities. It does not compute
EIRV, rank actions, or authorize anything — that stays in the decision
engine / policy engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from backend.models.governance import ModelVersion
from backend.repositories.governance import ModelVersionRepository
from ml.features.schema import ACTIONS
from ml.models.artifact import checksum as artifact_checksum
from ml.models.artifact import load_model
from ml.training.train import MODEL_ROLE


@dataclass
class RecoveryInference:
    """A loaded model bound to one exact ``ModelVersion`` id. ``model`` is
    the concrete class (RecoveryModel / TLearnerModel / ...); all expose
    ``predict(snapshot, action)`` and ``predict_all_actions(snapshot)``."""

    model_version_id: str
    model: Any
    feature_schema_id: str

    def predict(self, feature_snapshot: dict, action: str) -> float:
        return self.model.predict(feature_snapshot, action)

    def predict_all_actions(self, feature_snapshot: dict) -> dict[str, float]:
        """``{RETRY: p, MESSAGE: p, NO_ACTION: p}`` — one call per cycle."""
        return {a: self.model.predict(feature_snapshot, a) for a in ACTIONS}

    def incremental(self, feature_snapshot: dict) -> dict[str, float]:
        """``P(recovery|a) - P(recovery|NO_ACTION)`` per action. Derived,
        never persisted in ``Prediction`` and never a substitute for EIRV."""
        probs = self.predict_all_actions(feature_snapshot)
        base = probs["NO_ACTION"]
        return {a: probs[a] - base for a in ACTIONS}


@lru_cache(maxsize=8)
def _load_cached(artifact_ref: str, checksum: str | None):
    if checksum is not None:
        actual = artifact_checksum(artifact_ref)
        if actual != checksum:
            raise ValueError(
                f"artifact checksum mismatch for {artifact_ref}: "
                f"expected {checksum}, got {actual}"
            )
    return load_model(artifact_ref)


def load_for_model_version(mv: ModelVersion) -> RecoveryInference:
    if not mv.artifact_ref:
        raise ValueError(
            f"ModelVersion {mv.id} has no artifact_ref — cannot load a model"
        )
    model = _load_cached(mv.artifact_ref, mv.artifact_checksum)
    return RecoveryInference(
        model_version_id=str(mv.id),
        model=model,
        feature_schema_id=mv.feature_schema_id
        or getattr(model, "feature_schema_id", None),
    )


def load_promoted(db: Session, *, model_role: str = MODEL_ROLE) -> RecoveryInference:
    mv = ModelVersionRepository(db).promoted_for_role(model_role)
    if mv is None:
        raise LookupError(
            f"no PROMOTED ModelVersion for model_role={model_role!r}"
        )
    return load_for_model_version(mv)


def clear_cache() -> None:
    """Test helper — drop the artifact load cache."""
    _load_cached.cache_clear()
