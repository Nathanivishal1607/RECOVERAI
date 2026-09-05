"""Phase 4 — a Phase 4 model (T-learner) feeds the UNCHANGED Decision
Engine: train -> ModelVersion -> load from artifact -> decision cycle ->
3 Predictions (same exact ModelVersion) -> EIRV -> recommendation ->
policy veto -> final action. ML never chooses the final action or bypasses
policy; NO_ACTION never creates an Intervention.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.decision_engine.orchestrator import DecisionEngine
from backend.models import Base, enums
from backend.models.decision import DecisionRecord
from backend.policies.engine import PolicyContext
from backend.repositories import (
    CustomerRepository,
    MerchantRepository,
    ModelVersionRepository,
    PaymentEventRepository,
    PaymentRepository,
    PolicyRepository,
    RecoveryCaseRepository,
)
from ml.features.schema import FEATURE_SCHEMA_ID
from ml.inference.recovery import clear_cache, load_for_model_version, load_promoted
from ml.training.uplift import MODEL_ROLE, train_uplift_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

_SNAP = {
    "failure_category": "PAYMENT_METHOD_ISSUE",
    "failure_code": "SIM_INSTRUMENT_DECLINED",
    "payment_method": "CARD",
    "currency": "INR",
    "amount": 3200.0,
    "attempt_number": 1,
    "cust_hist_success_rate": 0.7,
    "cust_hist_failure_rate": 0.3,
    "cust_prev_recovery_rate": 0.4,
    "cust_tenure_days": 180,
    "cust_payment_freq_per_month": 2.5,
    "cust_segment": "casual",
    "minutes_since_last_attempt": 6.0,
    "hour_of_day": 10,
    "day_of_week": 1,
    "merchant_segment": "ecommerce",
    "merchant_hist_recovery_rate": 0.4,
    "merchant_avg_txn_amount": 2800.0,
    "_feature_schema_id": FEATURE_SCHEMA_ID,
}


@pytest.fixture()
def db_tmp(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    try:
        yield db, tmp_path
    finally:
        db.close()
        eng.dispose()
        clear_cache()


def test_t_learner_feeds_decision_engine(db_tmp):
    db, tmp = db_tmp
    run_simulation(
        db, replace(SimConfig(seed=8), n_cases=500, customers_per_merchant=130)
    )

    tr = train_uplift_model(
        db, kind="t_learner", version="t-e2e", seed=8, artifact_dir=tmp / "art"
    )
    mv = tr.model_version
    assert mv.status == enums.ModelVersionStatus.DRAFT.value
    assert mv.model_role == MODEL_ROLE
    assert mv.feature_schema_id == FEATURE_SCHEMA_ID
    assert mv.training_dataset_snapshot_id.startswith("tds-")
    assert mv.algorithm == "logistic_regression_per_action"

    repo = ModelVersionRepository(db)
    repo.transition_status(mv, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(mv, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()
    assert repo.promoted_for_role(MODEL_ROLE).id == mv.id

    clear_cache()
    predictor = load_promoted(db)
    assert predictor.model_version_id == str(mv.id)
    # reload determinism
    clear_cache()
    p2 = load_for_model_version(mv)
    for a in ("RETRY", "MESSAGE", "NO_ACTION"):
        assert predictor.predict(_SNAP, a) == p2.predict(_SNAP, a)
    # incremental helper is P(a) - P(NO_ACTION)
    probs = predictor.predict_all_actions(_SNAP)
    incr = predictor.incremental(_SNAP)
    assert incr["NO_ACTION"] == 0.0
    assert abs(incr["MESSAGE"] - (probs["MESSAGE"] - probs["NO_ACTION"])) < 1e-12

    # --- decision cycle -----------------------------------------------
    merchant = MerchantRepository(db).create(name="P4Co", industry="ecommerce")
    CustomerRepository(db).create(customer_id="C-P4", merchant_id=merchant.id)
    policy = PolicyRepository(db).create_version(
        policy_id=f"POL-{merchant.display_id}", policy_version="v1",
        merchant_id=merchant.id, max_retry_count=2, max_customer_contacts=2,
    )
    payment = PaymentRepository(db).create(
        merchant_id=merchant.id, customer_id="C-P4",
        amount=Decimal("3200.00"), currency="INR",
        status=enums.PaymentStatus.FAILED.value, payment_method="CARD",
    )
    pe = PaymentEventRepository(db)
    pe.append(payment_id=payment.id, event_type="PAYMENT_CREATED", event_timestamp=T0)
    pe.append(payment_id=payment.id, event_type="PAYMENT_FAILED",
              event_timestamp=T0 + timedelta(minutes=1), attempt_number=1)
    cases = RecoveryCaseRepository(db)
    case = cases.open_case(payment=payment, amount_at_risk=payment.amount,
                           failure_category="PAYMENT_METHOD_ISSUE",
                           failure_code="SIM_INSTRUMENT_DECLINED",
                           opened_at=T0 + timedelta(minutes=1))
    cases.transition(case, "ANALYZING")

    engine = DecisionEngine(db, predictor=predictor, model_version=mv)
    out = engine.run_cycle(
        case=case, feature_snapshot=_SNAP, policy=policy,
        policy_context=PolicyContext(retry_attempts_so_far=0, contacts_in_window=0,
                                     amount_at_risk=3200.0),
        decision_timestamp=T0 + timedelta(minutes=2),
    )
    db.commit()
    dr = out.decision_record

    preds = {p.action: p for p in dr.predictions}
    assert set(preds) == {"RETRY", "MESSAGE", "NO_ACTION"}
    assert all(p.model_version_id == mv.id for p in preds.values())
    # DecisionRecord has NO model-version column (unchanged contract)
    cols = {c.name for c in DecisionRecord.__table__.columns}
    assert "model_version" not in cols and "model_version_id" not in cols
    # recommendation vs final stored separately; value_context has NO_ACTION EIRV 0
    assert dr.recommended_action in ("RETRY", "MESSAGE", "NO_ACTION")
    assert dr.value_context[-1]["action"] == "NO_ACTION"
    assert dr.value_context[-1]["eirv_value"] == 0.0
    # Intervention only for RETRY/MESSAGE
    if dr.final_action in ("RETRY", "MESSAGE"):
        assert dr.intervention is not None and dr.intervention.action == dr.final_action
    else:
        assert dr.intervention is None

    # --- policy can still veto -> forced NO_ACTION, no Intervention ---
    cases.transition(case, "ACTION_SELECTED")
    cases.transition(case, "ACTION_EXECUTED")
    cases.transition(case, "WAITING_FOR_OUTCOME")
    cases.transition(case, "ANALYZING")
    blocked = PolicyRepository(db).create_version(
        policy_id=f"POL2-{merchant.display_id}", policy_version="v1",
        merchant_id=merchant.id, max_retry_count=0, max_customer_contacts=0,
        allowed_interventions=["RETRY", "MESSAGE"],
    )
    out2 = engine.run_cycle(
        case=case, feature_snapshot=_SNAP, policy=blocked,
        policy_context=PolicyContext(retry_attempts_so_far=9, contacts_in_window=9,
                                     amount_at_risk=3200.0),
        decision_timestamp=T0 + timedelta(hours=1),
    )
    db.commit()
    assert out2.final_action == "NO_ACTION"
    assert out2.decision_record.intervention is None
    assert out2.decision_record.cycle_number == 2

    # historical cycle 1 immutable
    db.refresh(dr)
    assert dr.cycle_number == 1 and len(dr.predictions) == 3
