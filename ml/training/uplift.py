"""Phase 4 — train any incremental/uplift candidate and register it as a
``ModelVersion`` (status DRAFT), reusing the Phase 3 infrastructure.

    train_uplift_model(db, kind="t_learner", version="v1", seed=42)

Same rules as Phase 3 (``docs/ml/learning-loop.md``): case-level split
from persisted ``TrainingExample`` rows, deterministic dataset-snapshot
id, one exact immutable artifact + sha256 on the ``ModelVersion``, no new
table, no ``model_version_id`` on ``DecisionRecord``. This module never
imports simulator hidden truth.

The artifact is written with the kind-tagged format
(``ml.models.artifact``) so ``ml.inference`` can load it through the
existing ``load_for_model_version`` path and feed the unchanged Decision
Engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models import enums
from backend.models.governance import ModelVersion
from backend.repositories.governance import ModelVersionRepository
from ml.data.dataset import DatasetSplit, build_dataset
from ml.evaluation.compare import observational_metrics
from ml.features.schema import FEATURE_SCHEMA_ID
from ml.models.artifact import checksum as artifact_checksum
from ml.models.artifact import save_model
from ml.models.recovery_model import ARTIFACT_DIR
from ml.models.uplift import ALL_KINDS, build_model

MODEL_ROLE = "recovery_prediction"  # same role as Phase 3 — one PROMOTED per role
TRAINING_PIPELINE_VERSION = "phase4-uplift-train-v1"

_NAME_BY_KIND = {
    "s_learner": "recovery-s-learner-logreg",
    "t_learner": "recovery-t-learner-logreg",
    "tree_s_learner": "recovery-tree-s-learner",
    "lgbm_s_learner": "recovery-lgbm-s-learner",
}


@dataclass
class UpliftTrainResult:
    model_version: ModelVersion
    model: object
    kind: str
    artifact_path: Path
    dataset: DatasetSplit
    evaluation: dict
    version: str
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        mv = self.model_version
        return {
            "model_version_id": str(mv.id),
            "model_role": mv.model_role,
            "model_name": mv.model_name,
            "kind": self.kind,
            "version": self.version,
            "status": mv.status,
            "artifact_ref": mv.artifact_ref,
            "artifact_checksum": mv.artifact_checksum,
            "training_dataset_snapshot_id": mv.training_dataset_snapshot_id,
            "feature_schema_id": mv.feature_schema_id,
            "n_train": self.dataset.n_train,
            "n_val": self.dataset.n_val,
            "n_test": self.dataset.n_test,
            "evaluation": self.evaluation,
            "notes": self.notes,
        }


def _default_version(kind: str) -> str:
    return f"{kind}-" + datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")


def train_uplift_model(
    db: Session,
    *,
    kind: str = "t_learner",
    version: str | None = None,
    seed: int = 42,
    ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
    artifact_dir: Path | None = None,
) -> UpliftTrainResult:
    if kind not in ALL_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {ALL_KINDS}")
    version = version or _default_version(kind)
    artifact_dir = Path(artifact_dir or ARTIFACT_DIR)
    model_name = _NAME_BY_KIND[kind]

    dataset = build_dataset(db, seed=seed, ratios=ratios)
    if dataset.n_train == 0:
        raise ValueError("training split is empty after case-level splitting")

    model = build_model(kind, dataset.rows_train, seed=seed)

    artifact_path = artifact_dir / f"{model_name}-{version}.joblib"
    save_model(model, artifact_path)
    checksum = artifact_checksum(artifact_path)

    evaluation = {
        "validation": observational_metrics(
            model, dataset.rows_val, name=kind
        ).as_dict(),
        "test": observational_metrics(model, dataset.rows_test, name=kind).as_dict(),
    }

    notes: list[str] = []
    if dataset.n_test == 0:
        notes.append("test split empty — dataset too small for a 3-way split")

    training_config = getattr(model, "params", None) or getattr(
        model, "train_config", {}
    )
    mv = ModelVersionRepository(db).create(
        model_role=MODEL_ROLE,
        model_name=model_name,
        version=version,
        status=enums.ModelVersionStatus.DRAFT.value,
        algorithm=model.algorithm,
        artifact_ref=str(artifact_path),
        artifact_checksum=checksum,
        training_dataset_snapshot_id=dataset.snapshot_id,
        feature_schema_id=FEATURE_SCHEMA_ID,
        training_config=dict(training_config) if training_config else None,
        training_pipeline_version=TRAINING_PIPELINE_VERSION,
        random_seed=seed,
        evaluation_summary=evaluation,
    )
    db.commit()

    return UpliftTrainResult(
        model_version=mv,
        model=model,
        kind=kind,
        artifact_path=artifact_path,
        dataset=dataset,
        evaluation=evaluation,
        version=version,
        notes=notes,
    )
