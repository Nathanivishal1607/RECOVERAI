"""Build the ML training matrix from persisted ``TrainingExample`` rows.

Contract compliance (Phase 1A.4 / ADR-012):

* Input is **only** persisted ``TrainingExample`` records.
* A row contributes a *label* only when ``is_observed`` is true and it
  carries an ``outcome_label`` (the actually-observed action of a
  resolved cycle). Un-observed candidate rows are *not* used as training
  targets — no manufactured counterfactuals.
* Features come from the immutable ``feature_snapshot`` frozen as of the
  ``DecisionRecord``; the candidate ``action`` is the treatment feature.
* Train / validation / test splitting is at ``recovery_case_id`` level —
  every row of a case lands in one split.
* Nothing hidden (latent reliability, potential outcomes, oracle EIRV,
  regime, future outcomes / cycles / PaymentEvents) is reachable here:
  this module imports neither ``simulation.ground_truth`` nor
  ``simulation.evaluation``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import enums
from backend.models.core_entities import RecoveryCase
from backend.models.training import TrainingExample
from ml.features.schema import FEATURE_SCHEMA_ID, assert_snapshot_clean, vectorize

_LABEL_TO_INT = {
    enums.OutcomeResult.NOT_RECOVERED.value: 0,
    enums.OutcomeResult.RECOVERED.value: 1,
}


@dataclass(frozen=True)
class TrainingRow:
    """One usable observation: (features, action) -> observed recovery.

    ``case_key`` is a run-stable identifier for the row's ``RecoveryCase``
    (the human-readable ``display_id`` such as ``RC-00042``, assigned by a
    deterministic creation-order counter) — used for reproducible
    case-level splitting. ``recovery_case_id`` is the random-UUID PK and is
    kept only for joining back to the DB.
    """

    training_example_id: str
    recovery_case_id: str
    case_key: str
    decision_record_id: str
    action: str
    label: int
    feature_snapshot: dict


@dataclass
class DatasetSplit:
    X_train: np.ndarray
    y_train: np.ndarray
    X_val: np.ndarray
    y_val: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray
    rows_train: list[TrainingRow]
    rows_val: list[TrainingRow]
    rows_test: list[TrainingRow]
    snapshot_id: str

    @property
    def n_train(self) -> int:
        return len(self.rows_train)

    @property
    def n_val(self) -> int:
        return len(self.rows_val)

    @property
    def n_test(self) -> int:
        return len(self.rows_test)


def load_training_rows(db: Session) -> list[TrainingRow]:
    """All *observed, labelled* ``TrainingExample`` rows, oldest first.

    Only rows where ``is_observed`` and ``outcome_label`` is set are
    returned — those are the real (features, action, outcome) triples.
    """
    stmt = (
        select(TrainingExample)
        .where(
            TrainingExample.is_observed.is_(True),
            TrainingExample.outcome_label.is_not(None),
        )
        .order_by(TrainingExample.created_at, TrainingExample.id)
    )
    # run-stable case key: RecoveryCase.display_id (deterministic counter)
    case_keys = {
        str(cid): disp
        for cid, disp in db.execute(
            select(RecoveryCase.id, RecoveryCase.display_id)
        ).all()
    }
    out: list[TrainingRow] = []
    for te in db.scalars(stmt):
        label = _LABEL_TO_INT.get(te.outcome_label)
        if label is None:  # defensive: unknown label value
            continue
        snap = dict(te.feature_snapshot or {})
        assert_snapshot_clean(snap)
        cid = str(te.recovery_case_id)
        out.append(
            TrainingRow(
                training_example_id=str(te.id),
                recovery_case_id=cid,
                case_key=case_keys.get(cid, cid),
                decision_record_id=str(te.decision_record_id),
                action=te.action,
                label=label,
                feature_snapshot=snap,
            )
        )
    return out


def split_by_case(
    rows: list[TrainingRow],
    *,
    seed: int = 42,
    ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
) -> tuple[list[TrainingRow], list[TrainingRow], list[TrainingRow]]:
    """Assign every *case* (not every row) to train/val/test. Deterministic
    given ``seed``. All rows of a case share one split — this mirrors
    ``backend/repositories/training.py::split_by_case`` (case-level, ADR-012).
    """
    assert abs(sum(ratios) - 1.0) < 1e-9, "ratios must sum to 1"
    # split on the run-stable case key so a fixed simulator seed gives a
    # reproducible split (RecoveryCase PKs are random UUIDs per run).
    case_keys = sorted({r.case_key for r in rows})
    rng = np.random.RandomState(seed)
    rng.shuffle(case_keys)
    n = len(case_keys)
    n_train = int(n * ratios[0])
    n_val = int(n * ratios[1])
    assignment: dict[str, str] = {}
    for i, key in enumerate(case_keys):
        if i < n_train:
            assignment[key] = "train"
        elif i < n_train + n_val:
            assignment[key] = "val"
        else:
            assignment[key] = "test"
    train = [r for r in rows if assignment[r.case_key] == "train"]
    val = [r for r in rows if assignment[r.case_key] == "val"]
    test = [r for r in rows if assignment[r.case_key] == "test"]
    return train, val, test


def _matrix(rows: list[TrainingRow]) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        return (
            np.empty((0, len(vectorize({}, "NO_ACTION"))), dtype=np.float64),
            np.empty((0,), dtype=int),
        )
    X = np.vstack([vectorize(r.feature_snapshot, r.action) for r in rows])
    y = np.asarray([r.label for r in rows], dtype=int)
    return X, y


def _row_fingerprint(r: TrainingRow) -> str:
    """Stable per-row content string — feature snapshot + action + label.

    Uses the *content* of the row, not its DB row id (a random UUID that
    changes every simulator run), so the snapshot id is reproducible for a
    fixed simulator seed / config (``backend/repositories/training.py``'s
    variant hashes row ids and is stable only within one run)."""
    snap = json.dumps(
        {k: r.feature_snapshot[k] for k in sorted(r.feature_snapshot)},
        sort_keys=True,
        default=str,
    )
    return f"{snap}|{r.action}|{r.label}"


def dataset_snapshot_id(rows: list[TrainingRow]) -> str:
    """Deterministic *content* hash of the exact training rows used —
    ``ModelVersion.training_dataset_snapshot_id`` (``tds-<n>-<hash>``).
    Reproducible across simulator runs with the same seed/config."""
    parts = sorted(_row_fingerprint(r) for r in rows)
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return f"tds-{len(rows)}-{digest[:16]}"


def build_dataset(
    db: Session,
    *,
    seed: int = 42,
    ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
) -> DatasetSplit:
    """End-to-end: load labelled rows -> case-level split -> matrices."""
    rows = load_training_rows(db)
    if not rows:
        raise ValueError(
            "no observed, labelled TrainingExample rows found — generate "
            "synthetic data first (python -m simulation.cli generate)"
        )
    train, val, test = split_by_case(rows, seed=seed, ratios=ratios)
    X_train, y_train = _matrix(train)
    X_val, y_val = _matrix(val)
    X_test, y_test = _matrix(test)
    return DatasetSplit(
        X_train=X_train,
        y_train=y_train,
        X_val=X_val,
        y_val=y_val,
        X_test=X_test,
        y_test=y_test,
        rows_train=train,
        rows_val=val,
        rows_test=test,
        snapshot_id=dataset_snapshot_id(rows),
    )


__all__ = [
    "TrainingRow",
    "DatasetSplit",
    "FEATURE_SCHEMA_ID",
    "load_training_rows",
    "split_by_case",
    "dataset_snapshot_id",
    "build_dataset",
]
