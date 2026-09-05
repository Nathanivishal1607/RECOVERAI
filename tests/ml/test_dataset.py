"""Phase 3 — training-data construction from persisted TrainingExamples."""

from __future__ import annotations

import numpy as np
import pytest

from backend.models import enums
from backend.models.training import TrainingExample
from ml.data.dataset import (
    build_dataset,
    dataset_snapshot_id,
    load_training_rows,
    split_by_case,
)
from ml.features.schema import assert_snapshot_clean, column_names, vectorize


def test_only_observed_labelled_rows_are_loaded(ml_run):
    db = ml_run["db"]
    rows = load_training_rows(db)
    assert rows, "expected some observed, labelled training rows"

    # every loaded row corresponds to an is_observed=True, labelled TE
    ids = {r.training_example_id for r in rows}
    all_tes = db.query(TrainingExample).all()
    for te in all_tes:
        if str(te.id) in ids:
            assert te.is_observed is True
            assert te.outcome_label in ("RECOVERED", "NOT_RECOVERED")
        else:
            # excluded rows are exactly the unobserved / unlabelled ones
            assert not (te.is_observed and te.outcome_label is not None)

    # labels are binary 0/1
    assert set(int(r.label) for r in rows) <= {0, 1}


def test_no_hidden_ground_truth_in_features(ml_run):
    rows = load_training_rows(ml_run["db"])
    forbidden = ("reliability", "p_by_action", "regime", "oracle", "potential",
                 "recovered", "recovery_amount", "outcome", "true_")
    for r in rows:
        assert_snapshot_clean(r.feature_snapshot)  # would raise
        keys = {k.lower() for k in r.feature_snapshot}
        assert not any(any(tok in k for tok in forbidden) for k in keys)


def test_case_level_split_keeps_a_case_in_one_split(ml_run):
    rows = load_training_rows(ml_run["db"])
    train, val, test = split_by_case(rows, seed=42)
    where = {}
    for name, part in (("train", train), ("val", val), ("test", test)):
        for r in part:
            where.setdefault(r.recovery_case_id, set()).add(name)
    assert all(len(s) == 1 for s in where.values()), "a case leaked across splits"
    # deterministic given the seed
    t2, v2, x2 = split_by_case(rows, seed=42)
    assert [r.training_example_id for r in t2] == [r.training_example_id for r in train]


def test_snapshot_id_is_deterministic_and_order_independent(ml_run):
    rows = load_training_rows(ml_run["db"])
    a = dataset_snapshot_id(rows)
    b = dataset_snapshot_id(list(reversed(rows)))
    assert a == b
    assert a.startswith(f"tds-{len(rows)}-")


def test_build_dataset_shapes_match_feature_columns(ml_run):
    ds = build_dataset(ml_run["db"], seed=1)
    ncols = len(column_names())
    assert ds.X_train.shape[1] == ncols
    assert ds.X_train.shape[0] == ds.n_train == len(ds.y_train)
    assert ds.n_train > 0 and ds.n_val >= 0 and ds.n_test >= 0
    # vectorize is stable
    r = ds.rows_train[0]
    assert np.array_equal(
        vectorize(r.feature_snapshot, r.action),
        vectorize(r.feature_snapshot, r.action),
    )


def test_build_dataset_raises_on_empty_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.models import Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True)()
    with pytest.raises(ValueError):
        build_dataset(db)
