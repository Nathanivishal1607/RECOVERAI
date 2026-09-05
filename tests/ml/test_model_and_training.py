"""Phase 3 — model training, three-action inference, ModelVersion,
artifact persistence/loading, lifecycle, determinism.

Uses a dedicated in-memory DB per test class/function where lifecycle is
mutated, so tests never contend over ``model_version`` uniqueness or a
shared committed session.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.core.errors import InvalidTransitionError, PromotedModelExistsError
from backend.models import Base, enums
from backend.repositories.governance import ModelVersionRepository
from ml.features.schema import ACTIONS, FEATURE_SCHEMA_ID
from ml.inference.recovery import clear_cache, load_for_model_version, load_promoted
from ml.models.recovery_model import RecoveryModel
from ml.training.train import MODEL_NAME, MODEL_ROLE, train_recovery_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation

_SNAP = {
    "failure_category": "TEMPORARY",
    "failure_code": "SIM_GATEWAY_TIMEOUT",
    "payment_method": "UPI",
    "currency": "INR",
    "amount": 1200.0,
    "attempt_number": 1,
    "cust_hist_success_rate": 0.82,
    "cust_hist_failure_rate": 0.18,
    "cust_prev_recovery_rate": 0.5,
    "cust_tenure_days": 260,
    "cust_payment_freq_per_month": 3.5,
    "cust_segment": "regular",
    "minutes_since_last_attempt": 4.0,
    "hour_of_day": 11,
    "day_of_week": 2,
    "merchant_segment": "saas_subscription",
    "merchant_hist_recovery_rate": 0.44,
    "merchant_avg_txn_amount": 1500.0,
    "_feature_schema_id": FEATURE_SCHEMA_ID,
}


def _fresh_db_with_data(seed: int, tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    run_simulation(
        db, replace(SimConfig(seed=seed), n_cases=420, customers_per_merchant=110)
    )
    return db, eng


@pytest.fixture()
def trained(tmp_path):
    db, eng = _fresh_db_with_data(seed=7, tmp_path=tmp_path)
    res = train_recovery_model(db, version="vT1", seed=7, artifact_dir=tmp_path / "art")
    try:
        yield {"db": db, "result": res, "art": tmp_path / "art"}
    finally:
        db.close()
        eng.dispose()
        clear_cache()


def test_training_produces_a_draft_model_version(trained):
    mv = trained["result"].model_version
    assert mv.model_role == MODEL_ROLE
    assert mv.model_name == MODEL_NAME
    assert mv.status == enums.ModelVersionStatus.DRAFT.value
    assert mv.feature_schema_id == FEATURE_SCHEMA_ID
    assert mv.training_dataset_snapshot_id.startswith("tds-")
    assert mv.artifact_ref and mv.artifact_checksum
    assert mv.training_config and mv.random_seed == 7
    assert "validation" in (mv.evaluation_summary or {})


def test_three_action_inference(trained):
    model = trained["result"].model
    probs = model.predict_all_actions(_SNAP)
    assert set(probs) == set(ACTIONS)
    for p in probs.values():
        assert 0.0 <= p <= 1.0
    assert max(probs.values()) - min(probs.values()) > 1e-3


def test_artifact_roundtrip_and_checksum(trained):
    res = trained["result"]
    assert res.artifact_path.exists()
    assert RecoveryModel.checksum(res.artifact_path) == res.model_version.artifact_checksum
    reloaded = RecoveryModel.load(res.artifact_path)
    for a in ACTIONS:
        assert abs(reloaded.predict(_SNAP, a) - res.model.predict(_SNAP, a)) < 1e-12


def test_inference_determinism_across_reloads(trained):
    mv = trained["result"].model_version
    clear_cache()
    p1 = load_for_model_version(mv)
    clear_cache()
    p2 = load_for_model_version(mv)
    for a in ACTIONS:
        assert p1.predict(_SNAP, a) == p2.predict(_SNAP, a)


def test_checksum_mismatch_is_rejected(trained):
    mv = trained["result"].model_version
    mv.artifact_checksum = "deadbeef" * 8
    clear_cache()
    with pytest.raises(ValueError):
        load_for_model_version(mv)


def test_forbidden_lifecycle_transitions(trained):
    repo = ModelVersionRepository(trained["db"])
    mv = trained["result"].model_version
    with pytest.raises(InvalidTransitionError):  # DRAFT -> PROMOTED
        repo.transition_status(mv, enums.ModelVersionStatus.PROMOTED.value)
    repo.transition_status(mv, enums.ModelVersionStatus.REJECTED.value)
    with pytest.raises(InvalidTransitionError):  # REJECTED -> PROMOTED
        repo.transition_status(mv, enums.ModelVersionStatus.PROMOTED.value)
    with pytest.raises(InvalidTransitionError):  # REJECTED -> VALIDATED
        repo.transition_status(mv, enums.ModelVersionStatus.VALIDATED.value)


def test_lifecycle_promote_and_one_promoted_per_role(trained):
    db, art = trained["db"], trained["art"]
    repo = ModelVersionRepository(db)
    mv = trained["result"].model_version
    repo.transition_status(mv, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(mv, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()
    assert repo.promoted_for_role(MODEL_ROLE).id == mv.id

    res2 = train_recovery_model(db, version="vT2", seed=9, artifact_dir=art)
    repo.transition_status(res2.model_version, enums.ModelVersionStatus.VALIDATED.value)
    with pytest.raises(PromotedModelExistsError):
        repo.transition_status(res2.model_version, enums.ModelVersionStatus.PROMOTED.value)

    # retire the incumbent, then the new one can be promoted (learning loop)
    repo.transition_status(mv, enums.ModelVersionStatus.RETIRED.value)
    repo.transition_status(res2.model_version, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()
    assert repo.promoted_for_role(MODEL_ROLE).id == res2.model_version.id

    clear_cache()
    assert load_promoted(db).model_version_id == str(res2.model_version.id)
