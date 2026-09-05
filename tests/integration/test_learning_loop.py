"""Phase 3 end-to-end learning loop:

    TrainingExamples -> case-level split -> train model -> ModelVersion
      -> load model from artifact -> new decision cycle
      -> 3 Predictions -> EIRV -> Recommendation -> Policy Evaluation
      -> Final Action -> (Intervention only for RETRY/MESSAGE)

Verifies every Phase 1A/1B invariant the prompt calls out.
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
from ml.training.train import MODEL_ROLE, train_recovery_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def loop_db(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    try:
        yield db, tmp_path
    finally:
        db.close()
        eng.dispose()
        clear_cache()


def test_end_to_end_learning_loop(loop_db):
    db, tmp = loop_db

    # --- Phase 2 data ------------------------------------------------
    res = run_simulation(
        db, replace(SimConfig(seed=4), n_cases=450, customers_per_merchant=120)
    )
    assert res.training_examples > 0

    # --- train -> ModelVersion (DRAFT) -> VALIDATED -> PROMOTED -----
    tr = train_recovery_model(db, version="loop-v1", seed=7, artifact_dir=tmp / "art")
    mv = tr.model_version
    assert mv.status == enums.ModelVersionStatus.DRAFT.value
    assert mv.training_dataset_snapshot_id.startswith("tds-")
    assert mv.feature_schema_id == FEATURE_SCHEMA_ID

    repo = ModelVersionRepository(db)
    repo.transition_status(mv, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(mv, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()
    assert repo.promoted_for_role(MODEL_ROLE).id == mv.id

    # --- load the model FROM ITS ARTIFACT (not "the current one") --
    clear_cache()
    predictor = load_promoted(db)
    assert predictor.model_version_id == str(mv.id)
    # repeated loading -> identical predictions for the same input
    clear_cache()
    predictor2 = load_for_model_version(mv)
    snap = _snapshot()
    for a in ("RETRY", "MESSAGE", "NO_ACTION"):
        assert predictor.predict(snap, a) == predictor2.predict(snap, a)

    # --- a brand-new decision cycle -------------------------------
    merchant = MerchantRepository(db).create(name="LoopCo", industry="edtech")
    CustomerRepository(db).create(customer_id="C-LOOP-1", merchant_id=merchant.id)
    policy = PolicyRepository(db).create_version(
        policy_id=f"POL-{merchant.display_id}", policy_version="v1",
        merchant_id=merchant.id, max_retry_count=2, max_customer_contacts=2,
    )
    payment = PaymentRepository(db).create(
        merchant_id=merchant.id, customer_id="C-LOOP-1",
        amount=Decimal("3000.00"), currency="INR",
        status=enums.PaymentStatus.FAILED.value, payment_method="CARD",
    )
    pe = PaymentEventRepository(db)
    pe.append(payment_id=payment.id, event_type="PAYMENT_CREATED", event_timestamp=T0)
    pe.append(payment_id=payment.id, event_type="PAYMENT_FAILED",
              event_timestamp=T0 + timedelta(minutes=1), attempt_number=1)
    cases = RecoveryCaseRepository(db)
    case = cases.open_case(payment=payment, amount_at_risk=payment.amount,
                           failure_category="CUSTOMER_ACTION_REQUIRED",
                           failure_code="SIM_AUTH_REQUIRED",
                           opened_at=T0 + timedelta(minutes=1))
    cases.transition(case, "ANALYZING")

    engine = DecisionEngine(db, predictor=predictor, model_version=mv)
    out = engine.run_cycle(
        case=case, feature_snapshot=snap, policy=policy,
        policy_context=PolicyContext(retry_attempts_so_far=0, contacts_in_window=0,
                                     amount_at_risk=3000.0),
        decision_timestamp=T0 + timedelta(minutes=2),
    )
    db.commit()

    dr = out.decision_record

    # --- invariants ------------------------------------------------
    # 3 predictions, one per candidate action, same exact ModelVersion
    preds = {p.action: p for p in dr.predictions}
    assert set(preds) == {"RETRY", "MESSAGE", "NO_ACTION"}
    assert all(p.model_version_id == mv.id for p in preds.values())
    # no hidden simulator truth in any persisted feature snapshot
    forbidden = ("reliability", "p_by_action", "regime", "oracle", "potential",
                 "recovered", "recovery_amount", "true_")
    for p in preds.values():
        assert not any(tok in k.lower() for k in p.feature_snapshot for tok in forbidden)

    # DecisionRecord has NO model-version column
    cols = {c.name for c in DecisionRecord.__table__.columns}
    assert "model_version" not in cols and "model_version_id" not in cols

    # recommendation vs final action are separate fields
    assert dr.recommended_action in ("RETRY", "MESSAGE", "NO_ACTION")
    assert dr.final_action in ("RETRY", "MESSAGE", "NO_ACTION")
    # value_context carries per-action EIRV + prob
    assert {vc["action"] for vc in dr.value_context} == {"RETRY", "MESSAGE", "NO_ACTION"}
    assert dr.value_context[2]["action"] == "NO_ACTION"
    assert dr.value_context[2]["eirv_value"] == 0.0

    # a PolicyEvaluation exists for every candidate the veto loop checked
    pe_actions = {pv.action for pv in dr.policy_evaluations}
    assert pe_actions == set(out.ranked_actions[: len(pe_actions)])
    assert out.ranked_actions[0] == dr.recommended_action

    # Intervention iff final_action in {RETRY, MESSAGE}
    if dr.final_action in ("RETRY", "MESSAGE"):
        assert dr.intervention is not None
        assert dr.intervention.action == dr.final_action
        assert dr.intervention.execution_status == "REQUESTED"
    else:
        assert dr.intervention is None  # NO_ACTION -> no Intervention

    # --- policy CAN still veto: force all interventions blocked ---
    cases.transition(case, "ACTION_SELECTED")
    cases.transition(case, "ACTION_EXECUTED")
    cases.transition(case, "WAITING_FOR_OUTCOME")
    cases.transition(case, "ANALYZING")  # re-evaluate -> NEW DecisionRecord
    blocked_policy = PolicyRepository(db).create_version(
        policy_id=f"POL2-{merchant.display_id}", policy_version="v1",
        merchant_id=merchant.id, max_retry_count=0, max_customer_contacts=0,
        allowed_interventions=["RETRY", "MESSAGE"],
    )
    out2 = engine.run_cycle(
        case=case, feature_snapshot=snap, policy=blocked_policy,
        policy_context=PolicyContext(retry_attempts_so_far=5, contacts_in_window=5,
                                     amount_at_risk=3000.0),
        decision_timestamp=T0 + timedelta(hours=1),
    )
    db.commit()
    assert out2.final_action == "NO_ACTION"          # veto forced NO_ACTION
    assert out2.decision_record.intervention is None  # NO_ACTION -> no Intervention
    assert out2.decision_record.cycle_number == 2     # re-eval -> new record

    # --- historical DecisionRecord D1 is immutable / untouched ----
    db.refresh(dr)
    assert dr.cycle_number == 1
    assert dr.recommended_action == out.recommended_action
    assert dr.final_action == out.final_action
    assert len(dr.predictions) == 3

    # two distinct immutable records under one case
    all_cycles = {d.cycle_number for d in db.query(DecisionRecord)
                  .filter(DecisionRecord.recovery_case_id == case.id).all()}
    assert all_cycles == {1, 2}


def _snapshot() -> dict:
    return {
        "failure_category": "CUSTOMER_ACTION_REQUIRED",
        "failure_code": "SIM_AUTH_REQUIRED",
        "payment_method": "CARD",
        "currency": "INR",
        "amount": 3000.0,
        "attempt_number": 1,
        "cust_hist_success_rate": 0.78,
        "cust_hist_failure_rate": 0.22,
        "cust_prev_recovery_rate": 0.45,
        "cust_tenure_days": 200,
        "cust_payment_freq_per_month": 2.0,
        "cust_segment": "casual",
        "minutes_since_last_attempt": 6.0,
        "hour_of_day": 9,
        "day_of_week": 1,
        "merchant_segment": "edtech",
        "merchant_hist_recovery_rate": 0.42,
        "merchant_avg_txn_amount": 2500.0,
        "_feature_schema_id": FEATURE_SCHEMA_ID,
    }
