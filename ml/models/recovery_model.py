"""The Phase 3 MVP recovery model.

`RecoveryModel` — a single scikit-learn ``Pipeline`` of
``StandardScaler -> LogisticRegression`` over
``[decision-time features  ⊕  one-hot(candidate action)]``. One shared
model, action as a treatment feature: an **S-learner** (see
``docs/ml/uplift-modelling.md``). It only produces ``Prediction``
probabilities — it does not compute EIRV and does not choose an action.

Deterministic: fixed ``random_state``, ``liblinear`` solver, no
multi-threading nondeterminism. Re-loading an artifact and re-scoring the
same input yields identical probabilities.

Persistence: ``joblib`` dump to ``ml/models/artifacts/<name>.joblib``
(git-ignored) + a sha256 of the artifact bytes for integrity /
reproducibility. Historical reconstruction never depends on "the latest"
model — a ``ModelVersion`` points at one exact artifact file + checksum.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.features.schema import ACTIONS, FEATURE_SCHEMA_ID, column_names, vectorize

#: Where trained artifacts live (see .gitignore: ``ml/models/artifacts/``).
ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "ml" / "models" / "artifacts"

#: Bumped if the artifact payload layout changes.
ARTIFACT_FORMAT = 1


@dataclass
class TrainConfig:
    """Hyperparameters — persisted verbatim onto the ``ModelVersion``."""

    C: float = 1.0
    penalty: str = "l2"
    solver: str = "liblinear"
    max_iter: int = 1000
    class_weight: str | None = None
    random_seed: int = 42

    def as_dict(self) -> dict:
        return {
            "C": self.C,
            "penalty": self.penalty,
            "solver": self.solver,
            "max_iter": self.max_iter,
            "class_weight": self.class_weight,
            "random_seed": self.random_seed,
        }


@dataclass
class RecoveryModel:
    """Wraps a fitted sklearn pipeline + the metadata needed to reproduce
    and trace it."""

    pipeline: Pipeline
    feature_schema_id: str = FEATURE_SCHEMA_ID
    feature_columns: list[str] = field(default_factory=column_names)
    train_config: dict = field(default_factory=lambda: TrainConfig().as_dict())
    algorithm: str = "logistic_regression"

    # ------------------------------------------------------------------ train
    @classmethod
    def train(
        cls,
        X: np.ndarray,
        y: np.ndarray,
        *,
        config: TrainConfig | None = None,
    ) -> "RecoveryModel":
        cfg = config or TrainConfig()
        pipe = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=cfg.C,
                        penalty=cfg.penalty,
                        solver=cfg.solver,
                        max_iter=cfg.max_iter,
                        class_weight=cfg.class_weight,
                        random_state=cfg.random_seed,
                    ),
                ),
            ]
        )
        pipe.fit(X, np.asarray(y, dtype=int))
        return cls(pipeline=pipe, train_config=cfg.as_dict())

    # -------------------------------------------------------------- inference
    def predict_proba_row(self, X: np.ndarray) -> np.ndarray:
        """P(recovery=1) for each row of a pre-vectorized matrix."""
        proba = self.pipeline.predict_proba(np.atleast_2d(X))
        # column for the positive class (label 1)
        classes = list(self.pipeline.classes_)
        pos = classes.index(1) if 1 in classes else (len(classes) - 1)
        return proba[:, pos]

    def predict(self, feature_snapshot: dict, action: str) -> float:
        """``P(recovery | features, action)`` — the single stable inference
        entry point the decision engine calls once per candidate action."""
        x = vectorize(feature_snapshot, action)
        return float(self.predict_proba_row(x)[0])

    def predict_all_actions(self, feature_snapshot: dict) -> dict[str, float]:
        """``{RETRY: p, MESSAGE: p, NO_ACTION: p}`` for one snapshot."""
        return {a: self.predict(feature_snapshot, a) for a in ACTIONS}

    # ------------------------------------------------------------- persistence
    def _payload(self) -> dict:
        return {
            "format": ARTIFACT_FORMAT,
            "algorithm": self.algorithm,
            "feature_schema_id": self.feature_schema_id,
            "feature_columns": self.feature_columns,
            "train_config": self.train_config,
            "pipeline": self.pipeline,
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._payload(), path, compress=3)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "RecoveryModel":
        payload = joblib.load(Path(path))
        if payload.get("format") != ARTIFACT_FORMAT:
            raise ValueError(
                f"unsupported artifact format {payload.get('format')!r} "
                f"(expected {ARTIFACT_FORMAT})"
            )
        return cls(
            pipeline=payload["pipeline"],
            feature_schema_id=payload["feature_schema_id"],
            feature_columns=list(payload["feature_columns"]),
            train_config=dict(payload["train_config"]),
            algorithm=payload["algorithm"],
        )

    @staticmethod
    def checksum(path: str | Path) -> str:
        """sha256 of the artifact file bytes — recorded on the
        ``ModelVersion`` for integrity / reproducibility."""
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
