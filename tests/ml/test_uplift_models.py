"""Phase 4 — incremental / uplift models: S-learner incremental,
T-learner, tree candidate, LightGBM candidate, NO_ACTION baseline,
no counterfactual fabrication, artifact roundtrip, decision-engine
compatibility."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models import Base, enums
from ml.data.dataset import build_dataset, load_training_rows
from ml.features.schema import ACTIONS, FEATURE_SCHEMA_ID
from ml.models.artifact import checksum, load_model, save_model
from ml.models.uplift import (
    ALL_KINDS,
    LGBMSLearnerModel,
    SLearnerModel,
    TLearnerModel,
    TreeSLearnerModel,
    build_model,
    lightgbm_available,
)
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation

_SNAP = {
    "failure_category": "CUSTOMER_ACTION_REQUIRED",
    "failure_code": "SIM_AUTH_REQUIRED",
    "payment_method": "CARD",
    "currency": "INR",
    "amount": 2200.0,
    "attempt_number": 1,
    "cust_hist_success_rate": 0.75,
    "cust_hist_failure_rate": 0.25,
    "cust_prev_recovery_rate": 0.45,
    "cust_tenure_days": 210,
    "cust_payment_freq_per_month": 3.0,
    "cust_segment": "regular",
    "minutes_since_last_attempt": 5.0,
    "hour_of_day": 11,
    "day_of_week": 2,
    "merchant_segment": "saas_subscription",
    "merchant_hist_recovery_rate": 0.45,
    "merchant_avg_txn_amount": 1800.0,
    "_feature_schema_id": FEATURE_SCHEMA_ID,
}


@pytest.fixture(scope="module")
def rows():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    run_simulation(
        db, replace(SimConfig(seed=17), n_cases=500, customers_per_merchant=130)
    )
    ds = build_dataset(db, seed=17)
    try:
        yield {"db": db, "ds": ds}
    finally:
        db.close()
        eng.dispose()


def _check_common(model):
    probs = model.predict_all_actions(_SNAP)
    assert set(probs) == set(ACTIONS)
    for p in probs.values():
        assert 0.0 <= p <= 1.0
    incr = model.incremental(_SNAP)
    assert set(incr) == set(ACTIONS)
    # incremental is exactly P(a) - P(NO_ACTION); NO_ACTION incr == 0
    assert incr["NO_ACTION"] == 0.0
    for a in ("RETRY", "MESSAGE"):
        assert abs(incr[a] - (probs[a] - probs["NO_ACTION"])) < 1e-12
    # incremental probability is NOT clamped to [0,1] — it's a difference
    assert all(-1.0 <= v <= 1.0 for v in incr.values())


def test_s_learner_incremental(rows):
    m = SLearnerModel.train(rows["ds"].rows_train)
    _check_common(m)
    assert m.name == "s_learner"


def test_t_learner_predictions_and_per_action_heads(rows):
    m = TLearnerModel.train(rows["ds"].rows_train)
    _check_common(m)
    # one head per action that had >=2 rows and both classes
    assert set(m.heads).issubset(set(ACTIONS))
    assert all(m.n_rows_per_action[a] > 0 for a in ACTIONS)
    # a head with no rows would fall back to a base rate in [0,1]
    for a in ACTIONS:
        assert 0.0 <= m.fallback_rate[a] <= 1.0


def test_tree_candidate(rows):
    m = TreeSLearnerModel.train(rows["ds"].rows_train, max_depth=4)
    _check_common(m)
    assert m.algorithm == "decision_tree_s_learner"


@pytest.mark.skipif(not lightgbm_available(), reason="lightgbm not installed")
def test_lgbm_candidate_deterministic(rows):
    m1 = LGBMSLearnerModel.train(rows["ds"].rows_train, random_seed=17)
    m2 = LGBMSLearnerModel.train(rows["ds"].rows_train, random_seed=17)
    _check_common(m1)
    for a in ACTIONS:
        assert m1.predict(_SNAP, a) == m2.predict(_SNAP, a)


def test_no_counterfactual_labels_used(rows):
    """Training rows are the observed-action rows only; every candidate is
    fed via ``build_model`` which consumes exactly those rows."""
    all_rows = load_training_rows(rows["db"])
    # every row is an observed, labelled example
    assert all(r.label in (0, 1) for r in all_rows)
    # the dataset builder never emits an unlabelled row as a target
    ds = rows["ds"]
    assert len(ds.y_train) == ds.n_train
    assert set(np.unique(ds.y_train)) <= {0, 1}


def test_case_level_split_preserved(rows):
    ds = rows["ds"]
    train_cases = {r.recovery_case_id for r in ds.rows_train}
    val_cases = {r.recovery_case_id for r in ds.rows_val}
    test_cases = {r.recovery_case_id for r in ds.rows_test}
    assert not (train_cases & val_cases)
    assert not (train_cases & test_cases)
    assert not (val_cases & test_cases)


@pytest.mark.parametrize("kind", ["s_learner", "t_learner", "tree_s_learner"])
def test_artifact_roundtrip_all_kinds(rows, tmp_path, kind):
    m = build_model(kind, rows["ds"].rows_train, seed=17)
    path = tmp_path / f"{kind}.joblib"
    save_model(m, path)
    assert path.exists()
    cs = checksum(path)
    assert len(cs) == 64
    reloaded = load_model(path)
    for a in ACTIONS:
        assert abs(reloaded.predict(_SNAP, a) - m.predict(_SNAP, a)) < 1e-12


@pytest.mark.skipif(not lightgbm_available(), reason="lightgbm not installed")
def test_artifact_roundtrip_lgbm(rows, tmp_path):
    m = build_model("lgbm_s_learner", rows["ds"].rows_train, seed=17)
    path = tmp_path / "lgbm.joblib"
    save_model(m, path)
    reloaded = load_model(path)
    for a in ACTIONS:
        assert abs(reloaded.predict(_SNAP, a) - m.predict(_SNAP, a)) < 1e-12


def test_build_model_rejects_unknown_kind(rows):
    with pytest.raises(ValueError):
        build_model("mystery_learner", rows["ds"].rows_train)


def test_all_kinds_constant():
    assert ALL_KINDS == (
        "s_learner", "t_learner", "tree_s_learner", "lgbm_s_learner"
    )
