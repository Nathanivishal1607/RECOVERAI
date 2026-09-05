"""Phase 4 incremental / uplift models.

All models share one interface: given a decision-time feature snapshot they
return ``P(recovery | features, action)`` for RETRY / MESSAGE / NO_ACTION.
The *incremental* probability is then derived (never stored in
``Prediction``, never an ML score that replaces EIRV):

    incremental(action) = P(recovery | features, action)
                          - P(recovery | features, NO_ACTION)

Models
------
* ``SLearnerModel``   — the Phase 3 baseline (``RecoveryModel``), re-exported
                        here with the common ``incremental()`` helper.
* ``TLearnerModel``   — one calibrated logistic-regression head per action,
                        each trained ONLY on that action's observed rows.
* ``TreeSLearnerModel`` — a shallow ``DecisionTreeClassifier`` S-learner
                        (action as a one-hot input feature) — the "tree /
                        uplift candidate" done cleanly with the existing
                        stack (no custom causal framework).
* ``LGBMSLearnerModel`` — a gradient-boosted (LightGBM) action-conditioned
                        S-learner, if ``lightgbm`` is importable.

Every model respects the observational ``TrainingExample`` contract: it is
fed only ``(features, observed_action, observed_outcome)`` rows produced by
``ml.data.dataset``; no counterfactual labels are manufactured, features
are decision-time only, splitting is case-level (done upstream).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier
from sklearn.preprocessing import StandardScaler

from ml.data.dataset import TrainingRow
from ml.features.schema import (
    ACTIONS,
    FEATURE_SCHEMA_ID,
    vectorize,
    vectorize_features,
)
from ml.models.recovery_model import RecoveryModel, TrainConfig

NO_ACTION = "NO_ACTION"

try:  # optional dependency — evaluated, not required
    from lightgbm import LGBMClassifier

    _HAS_LGBM = True
except Exception:  # pragma: no cover - env without lightgbm
    LGBMClassifier = None  # type: ignore
    _HAS_LGBM = False


@runtime_checkable
class IncrementalModel(Protocol):
    """Common inference surface for every Phase 4 candidate."""

    name: str
    algorithm: str
    feature_schema_id: str

    def predict(self, feature_snapshot: dict, action: str) -> float: ...

    def predict_all_actions(self, feature_snapshot: dict) -> dict[str, float]: ...

    def incremental(self, feature_snapshot: dict) -> dict[str, float]: ...


def _incremental_from_probs(probs: dict[str, float]) -> dict[str, float]:
    base = probs[NO_ACTION]
    return {a: probs[a] - base for a in ACTIONS}


def _proba1(clf, X: np.ndarray) -> np.ndarray:
    """P(class == 1) for a fitted sklearn-style classifier, robust to a
    single-class training fold."""
    classes = list(getattr(clf, "classes_", [0, 1]))
    proba = clf.predict_proba(np.atleast_2d(X))
    if 1 in classes:
        return proba[:, classes.index(1)]
    # the head only ever saw class 0 -> P(recovery) is ~0
    return np.zeros(proba.shape[0])


# --------------------------------------------------------------------------- S
@dataclass
class SLearnerModel:
    """Thin adapter around the Phase 3 ``RecoveryModel`` giving it the
    Phase 4 common interface + ``incremental()``."""

    model: RecoveryModel
    name: str = "s_learner"
    feature_schema_id: str = FEATURE_SCHEMA_ID

    @property
    def algorithm(self) -> str:
        return self.model.algorithm

    @classmethod
    def train(cls, rows: list[TrainingRow], *, config: TrainConfig | None = None) -> "SLearnerModel":
        X = np.vstack([vectorize(r.feature_snapshot, r.action) for r in rows])
        y = np.asarray([r.label for r in rows], dtype=int)
        return cls(model=RecoveryModel.train(X, y, config=config))

    def predict(self, feature_snapshot: dict, action: str) -> float:
        return self.model.predict(feature_snapshot, action)

    def predict_all_actions(self, feature_snapshot: dict) -> dict[str, float]:
        return self.model.predict_all_actions(feature_snapshot)

    def incremental(self, feature_snapshot: dict) -> dict[str, float]:
        return _incremental_from_probs(self.predict_all_actions(feature_snapshot))


# --------------------------------------------------------------------------- T
@dataclass
class TLearnerModel:
    """One StandardScaler->LogisticRegression head per action, each trained
    only on rows where that action was the observed action. Actions with no
    (or single-class) training rows fall back to a constant base rate."""

    heads: dict[str, Pipeline]
    fallback_rate: dict[str, float]
    name: str = "t_learner"
    algorithm: str = "logistic_regression_per_action"
    feature_schema_id: str = FEATURE_SCHEMA_ID
    n_rows_per_action: dict[str, int] = field(default_factory=dict)

    @classmethod
    def train(
        cls, rows: list[TrainingRow], *, config: TrainConfig | None = None
    ) -> "TLearnerModel":
        cfg = config or TrainConfig()
        heads: dict[str, Pipeline] = {}
        fallback: dict[str, float] = {}
        counts: dict[str, int] = {}
        global_rate = (
            float(np.mean([r.label for r in rows])) if rows else 0.0
        )
        for action in ACTIONS:
            sub = [r for r in rows if r.action == action]
            counts[action] = len(sub)
            if sub:
                fallback[action] = float(np.mean([r.label for r in sub]))
            else:
                fallback[action] = global_rate
            if len(sub) >= 2 and len({r.label for r in sub}) == 2:
                X = np.vstack([vectorize_features(r.feature_snapshot) for r in sub])
                y = np.asarray([r.label for r in sub], dtype=int)
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
                pipe.fit(X, y)
                heads[action] = pipe
        return cls(
            heads=heads, fallback_rate=fallback, n_rows_per_action=counts
        )

    def predict(self, feature_snapshot: dict, action: str) -> float:
        head = self.heads.get(action)
        if head is None:
            return float(max(0.0, min(1.0, self.fallback_rate.get(action, 0.0))))
        x = vectorize_features(feature_snapshot)
        p = head.predict_proba(np.atleast_2d(x))
        classes = list(head.named_steps["clf"].classes_)
        p1 = p[:, classes.index(1)] if 1 in classes else np.zeros(p.shape[0])
        return float(max(0.0, min(1.0, p1[0])))

    def predict_all_actions(self, feature_snapshot: dict) -> dict[str, float]:
        return {a: self.predict(feature_snapshot, a) for a in ACTIONS}

    def incremental(self, feature_snapshot: dict) -> dict[str, float]:
        return _incremental_from_probs(self.predict_all_actions(feature_snapshot))


# ------------------------------------------------------------------- tree (S)
@dataclass
class TreeSLearnerModel:
    """Shallow decision-tree S-learner (action is a one-hot input feature).

    This is the Phase 4 "tree / uplift candidate": a clean, dependency-free
    tree model whose per-action probability differences act as an implicit
    uplift estimate — NOT a bespoke causal-tree framework.
    """

    tree: DecisionTreeClassifier
    name: str = "tree_s_learner"
    algorithm: str = "decision_tree_s_learner"
    feature_schema_id: str = FEATURE_SCHEMA_ID
    params: dict = field(default_factory=dict)

    @classmethod
    def train(
        cls,
        rows: list[TrainingRow],
        *,
        max_depth: int = 5,
        min_samples_leaf: int = 20,
        random_seed: int = 42,
    ) -> "TreeSLearnerModel":
        X = np.vstack([vectorize(r.feature_snapshot, r.action) for r in rows])
        y = np.asarray([r.label for r in rows], dtype=int)
        tree = DecisionTreeClassifier(
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            random_state=random_seed,
        )
        tree.fit(X, y)
        return cls(
            tree=tree,
            params={
                "max_depth": max_depth,
                "min_samples_leaf": min_samples_leaf,
                "random_seed": random_seed,
            },
        )

    def predict(self, feature_snapshot: dict, action: str) -> float:
        x = vectorize(feature_snapshot, action)
        return float(_proba1(self.tree, x)[0])

    def predict_all_actions(self, feature_snapshot: dict) -> dict[str, float]:
        return {a: self.predict(feature_snapshot, a) for a in ACTIONS}

    def incremental(self, feature_snapshot: dict) -> dict[str, float]:
        return _incremental_from_probs(self.predict_all_actions(feature_snapshot))


# ------------------------------------------------------------------ LightGBM (S)
def lightgbm_available() -> bool:
    return _HAS_LGBM


@dataclass
class LGBMSLearnerModel:
    """Action-conditioned LightGBM S-learner (action one-hot appended to
    the feature vector). Only constructible when ``lightgbm`` is installed.
    Deterministic: single-threaded, fixed seed, deterministic flag."""

    booster: "LGBMClassifier"
    name: str = "lgbm_s_learner"
    algorithm: str = "lightgbm_s_learner"
    feature_schema_id: str = FEATURE_SCHEMA_ID
    params: dict = field(default_factory=dict)

    @classmethod
    def train(
        cls,
        rows: list[TrainingRow],
        *,
        n_estimators: int = 200,
        learning_rate: float = 0.05,
        num_leaves: int = 15,
        max_depth: int = 4,
        min_child_samples: int = 30,
        random_seed: int = 42,
    ) -> "LGBMSLearnerModel":
        if not _HAS_LGBM:  # pragma: no cover
            raise RuntimeError("lightgbm is not installed")
        X = np.vstack([vectorize(r.feature_snapshot, r.action) for r in rows])
        y = np.asarray([r.label for r in rows], dtype=int)
        params = dict(
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            num_leaves=num_leaves,
            max_depth=max_depth,
            min_child_samples=min_child_samples,
            random_state=random_seed,
            n_jobs=1,
            deterministic=True,
            force_row_wise=True,
            verbose=-1,
        )
        clf = LGBMClassifier(**params)
        clf.fit(X, y)
        return cls(booster=clf, params=params)

    def predict(self, feature_snapshot: dict, action: str) -> float:
        x = vectorize(feature_snapshot, action)
        return float(_proba1(self.booster, x)[0])

    def predict_all_actions(self, feature_snapshot: dict) -> dict[str, float]:
        return {a: self.predict(feature_snapshot, a) for a in ACTIONS}

    def incremental(self, feature_snapshot: dict) -> dict[str, float]:
        return _incremental_from_probs(self.predict_all_actions(feature_snapshot))


# --------------------------------------------------------------------------- factory
def build_model(kind: str, rows: list[TrainingRow], *, seed: int = 42):
    """Train and return one candidate by name. Unknown / unavailable kinds
    raise so the caller can record 'not evaluated'."""
    if kind == "s_learner":
        return SLearnerModel.train(rows, config=TrainConfig(random_seed=seed))
    if kind == "t_learner":
        return TLearnerModel.train(rows, config=TrainConfig(random_seed=seed))
    if kind == "tree_s_learner":
        return TreeSLearnerModel.train(rows, random_seed=seed)
    if kind == "lgbm_s_learner":
        if not _HAS_LGBM:
            raise RuntimeError("lightgbm not installed")
        return LGBMSLearnerModel.train(rows, random_seed=seed)
    raise ValueError(f"unknown model kind {kind!r}")


ALL_KINDS = ("s_learner", "t_learner", "tree_s_learner", "lgbm_s_learner")
