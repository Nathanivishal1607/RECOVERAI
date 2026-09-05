"""Unified model-artifact save/load + checksum.

Every Phase 3/4 model persists a joblib payload tagged with a ``kind`` so
inference can rebuild the right class from an immutable ``ModelVersion``.
Historical reconstruction never depends on "the latest" model — a
``ModelVersion`` points at one exact file + sha256.

Supported ``kind`` values:
    "s_learner"       -> ml.models.recovery_model.RecoveryModel
    "t_learner"       -> ml.models.uplift.TLearnerModel
    "tree_s_learner"  -> ml.models.uplift.TreeSLearnerModel
    "lgbm_s_learner"  -> ml.models.uplift.LGBMSLearnerModel
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import joblib

ARTIFACT_FORMAT = 2  # v1 = Phase 3 RecoveryModel-only; v2 = kind-tagged union


def checksum(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def save_model(model, path: str | Path) -> Path:
    """Persist any supported model. Dispatches on ``model.name`` /
    duck-typed attributes; the payload records ``kind`` + ``format``."""
    from ml.models.recovery_model import RecoveryModel
    from ml.models.uplift import (
        LGBMSLearnerModel,
        SLearnerModel,
        TLearnerModel,
        TreeSLearnerModel,
    )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(model, SLearnerModel):
        model = model.model  # unwrap to the underlying RecoveryModel

    if isinstance(model, RecoveryModel):
        payload = {
            "format": ARTIFACT_FORMAT,
            "kind": "s_learner",
            "algorithm": model.algorithm,
            "feature_schema_id": model.feature_schema_id,
            "feature_columns": model.feature_columns,
            "train_config": model.train_config,
            "pipeline": model.pipeline,
        }
    elif isinstance(model, TLearnerModel):
        payload = {
            "format": ARTIFACT_FORMAT,
            "kind": "t_learner",
            "algorithm": model.algorithm,
            "feature_schema_id": model.feature_schema_id,
            "heads": model.heads,
            "fallback_rate": model.fallback_rate,
            "n_rows_per_action": model.n_rows_per_action,
        }
    elif isinstance(model, TreeSLearnerModel):
        payload = {
            "format": ARTIFACT_FORMAT,
            "kind": "tree_s_learner",
            "algorithm": model.algorithm,
            "feature_schema_id": model.feature_schema_id,
            "tree": model.tree,
            "params": model.params,
        }
    elif isinstance(model, LGBMSLearnerModel):
        payload = {
            "format": ARTIFACT_FORMAT,
            "kind": "lgbm_s_learner",
            "algorithm": model.algorithm,
            "feature_schema_id": model.feature_schema_id,
            "booster": model.booster,
            "params": model.params,
        }
    else:  # pragma: no cover - defensive
        raise TypeError(f"cannot save unsupported model type {type(model)!r}")

    joblib.dump(payload, path, compress=3)
    return path


def load_model(path: str | Path):
    """Rebuild a model from its artifact. Returns the concrete class
    instance (``RecoveryModel`` / ``TLearnerModel`` / ...)."""
    from ml.models.recovery_model import RecoveryModel
    from ml.models.uplift import LGBMSLearnerModel, TLearnerModel, TreeSLearnerModel

    payload = joblib.load(Path(path))
    fmt = payload.get("format")
    if fmt not in (1, ARTIFACT_FORMAT):
        raise ValueError(f"unsupported artifact format {fmt!r}")

    kind = payload.get("kind", "s_learner")  # v1 artifacts are S-learners
    if kind == "s_learner":
        return RecoveryModel(
            pipeline=payload["pipeline"],
            feature_schema_id=payload["feature_schema_id"],
            feature_columns=list(payload["feature_columns"]),
            train_config=dict(payload["train_config"]),
            algorithm=payload["algorithm"],
        )
    if kind == "t_learner":
        return TLearnerModel(
            heads=payload["heads"],
            fallback_rate=payload["fallback_rate"],
            n_rows_per_action=payload.get("n_rows_per_action", {}),
            algorithm=payload["algorithm"],
            feature_schema_id=payload["feature_schema_id"],
        )
    if kind == "tree_s_learner":
        return TreeSLearnerModel(
            tree=payload["tree"],
            params=payload.get("params", {}),
            algorithm=payload["algorithm"],
            feature_schema_id=payload["feature_schema_id"],
        )
    if kind == "lgbm_s_learner":
        return LGBMSLearnerModel(
            booster=payload["booster"],
            params=payload.get("params", {}),
            algorithm=payload["algorithm"],
            feature_schema_id=payload["feature_schema_id"],
        )
    raise ValueError(f"unknown artifact kind {kind!r}")
