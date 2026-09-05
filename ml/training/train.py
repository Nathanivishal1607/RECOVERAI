"""Train the MVP recovery model and register it as a ``ModelVersion``.

Flow (``docs/ml/learning-loop.md`` Step 1-4):

    persisted TrainingExamples
        -> case-level split (by recovery_case_id)
        -> fit RecoveryModel (logistic-regression S-learner)
        -> write artifact  ml/models/artifacts/<name>.joblib  (+ sha256)
        -> evaluate on the held-out validation + test splits (observational)
        -> ModelVersionRepository.create(status=DRAFT)
             model_role = "recovery_prediction"
             training_dataset_snapshot_id = deterministic content hash
             feature_schema_id           = "sim-feature-schema-v1"
             artifact_ref / artifact_checksum / training_config /
             training_pipeline_version / random_seed / evaluation_summary

No new table, no ``model_version_id`` on ``DecisionRecord`` — the existing
``Prediction -> exact ModelVersion`` rule is untouched. This module never
imports simulator hidden truth.
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
from ml.evaluation.evaluate import evaluate_model
from ml.features.schema import FEATURE_SCHEMA_ID
from ml.models.recovery_model import ARTIFACT_DIR, RecoveryModel, TrainConfig

MODEL_ROLE = "recovery_prediction"
MODEL_NAME = "recovery-s-learner-logreg"
TRAINING_PIPELINE_VERSION = "phase3-train-v1"


@dataclass
class TrainResult:
    model_version: ModelVersion
    model: RecoveryModel
    artifact_path: Path
    dataset: DatasetSplit
    evaluation: dict
    version: str
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "model_version_id": str(self.model_version.id),
            "model_role": self.model_version.model_role,
            "model_name": self.model_version.model_name,
            "version": self.version,
            "status": self.model_version.status,
            "artifact_ref": self.model_version.artifact_ref,
            "artifact_checksum": self.model_version.artifact_checksum,
            "training_dataset_snapshot_id": (
                self.model_version.training_dataset_snapshot_id
            ),
            "feature_schema_id": self.model_version.feature_schema_id,
            "n_train": self.dataset.n_train,
            "n_val": self.dataset.n_val,
            "n_test": self.dataset.n_test,
            "evaluation": self.evaluation,
            "notes": self.notes,
        }


def _default_version() -> str:
    return "v" + datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")


def train_recovery_model(
    db: Session,
    *,
    version: str | None = None,
    config: TrainConfig | None = None,
    seed: int = 42,
    ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
    artifact_dir: Path | None = None,
) -> TrainResult:
    cfg = config or TrainConfig(random_seed=seed)
    version = version or _default_version()
    artifact_dir = Path(artifact_dir or ARTIFACT_DIR)

    dataset = build_dataset(db, seed=seed, ratios=ratios)
    if dataset.n_train == 0:
        raise ValueError("training split is empty after case-level splitting")

    model = RecoveryModel.train(dataset.X_train, dataset.y_train, config=cfg)

    artifact_path = artifact_dir / f"{MODEL_NAME}-{version}.joblib"
    model.save(artifact_path)
    checksum = RecoveryModel.checksum(artifact_path)

    evaluation = evaluate_model(
        model, rows_val=dataset.rows_val, rows_test=dataset.rows_test
    )

    notes: list[str] = []
    if dataset.n_test == 0:
        notes.append("test split empty — dataset too small for a 3-way split")
    for scope in ("validation", "test"):
        notes.extend(f"{scope}: {m}" for m in evaluation[scope].get("notes", []))

    mv = ModelVersionRepository(db).create(
        model_role=MODEL_ROLE,
        model_name=MODEL_NAME,
        version=version,
        status=enums.ModelVersionStatus.DRAFT.value,
        algorithm=model.algorithm,
        artifact_ref=str(artifact_path),
        artifact_checksum=checksum,
        training_dataset_snapshot_id=dataset.snapshot_id,
        feature_schema_id=FEATURE_SCHEMA_ID,
        training_config=model.train_config,
        training_pipeline_version=TRAINING_PIPELINE_VERSION,
        random_seed=cfg.random_seed,
        evaluation_summary=evaluation,
    )
    db.commit()

    return TrainResult(
        model_version=mv,
        model=model,
        artifact_path=artifact_path,
        dataset=dataset,
        evaluation=evaluation,
        version=version,
        notes=notes,
    )
