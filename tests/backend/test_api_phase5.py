"""Phase 5 — the recovery HTTP API.

    POST /payments            -> POST /payments/{id}/evaluate
    GET  /decisions/{id}      (full audit chain)
    POST /decisions/{id}/execute
    POST /decisions/{id}/outcome
    POST /cases/{id}/reevaluate
    GET  /cases/{id}

Uses FastAPI's TestClient with ``get_db`` overridden to the test session.
One module-scoped simulator run + promoted T-learner; each test uses its
own merchant / customer / payment.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.main import app
from backend.database.session import get_db
from backend.models import Base, enums
from backend.repositories.core import CustomerRepository, MerchantRepository
from backend.repositories.governance import ModelVersionRepository, PolicyRepository
from ml.inference.recovery import clear_cache
from ml.training.uplift import train_uplift_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation


@pytest.fixture(scope="module")
def api_client(tmp_path_factory):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk(c, _):  # noqa: ANN001
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    clear_cache()

    run_simulation(
        db, replace(SimConfig(seed=42), n_cases=1200, customers_per_merchant=250)
    )
    tr = train_uplift_model(
        db, kind="t_learner", version="p5-api", seed=42,
        artifact_dir=tmp_path_factory.mktemp("p5apiart"),
    )
    repo = ModelVersionRepository(db)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()

    def _override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    try:
        yield client, db
    finally:
        app.dependency_overrides.clear()
        db.close()
        eng.dispose()
        clear_cache()


def _merchant_customer_policy(db, tag: str):
    m = MerchantRepository(db).create(name=f"ApiCo-{tag}", industry="ecommerce")
    CustomerRepository(db).create(
        customer_id=f"AC-{tag}", merchant_id=m.id,
        transaction_count=40, successful_transactions=20, failed_transactions=20,
        historical_recovery_rate=Decimal("0.3"),
    )
    pol = PolicyRepository(db).create_version(
        policy_id=f"POL-{m.display_id}", policy_version="v1", merchant_id=m.id,
        max_retry_count=3, max_customer_contacts=3,
        allowed_interventions=["RETRY", "MESSAGE"],
    )
    db.commit()
    return m, f"AC-{tag}", pol


def test_health_still_ok(api_client):
    client, _ = api_client
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_full_api_flow(api_client):
    client, db = api_client
    m, cust, _pol = _merchant_customer_policy(db, "flow")

    # 1) create a failed payment
    r = client.post("/payments", json={
        "merchant_id": str(m.id), "customer_id": cust,
        "amount": "2500.00", "currency": "INR", "payment_method": "CARD",
        "failure_category": "TEMPORARY", "failure_code": "SIM_GATEWAY_TIMEOUT",
    })
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]
    assert r.json()["status"] == "FAILED"

    # 2) evaluate recovery -> one decision cycle
    r = client.post(f"/payments/{payment_id}/evaluate", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cycle_number"] == 1
    assert body["recommended_action"] in ("RETRY", "MESSAGE", "NO_ACTION")
    dr_id = body["decision_record_id"]
    case_id = body["recovery_case_id"]

    # audit chain in the evaluate response
    audit = body["audit"]
    assert {a["action"] for a in audit["actions_considered"]} == {
        "RETRY", "MESSAGE", "NO_ACTION"
    }
    assert audit["model_version"]["status"] == "PROMOTED"
    assert audit["actions_considered"][-1]["action"] == "NO_ACTION"

    # 3) retrieve the decision record with its audit chain
    r = client.get(f"/decisions/{dr_id}")
    assert r.status_code == 200
    got = r.json()
    assert got["decision_record_id"] == dr_id
    assert got["cycle_number"] == 1
    assert got["model_version"]["model_role"] == "recovery_prediction"
    for a in got["actions_considered"]:
        if a["action"] == "NO_ACTION":
            assert a["incremental_probability"] == 0.0
            assert a["eirv_value"] == 0.0

    # 4) execute / mock the selected action
    r = client.post(f"/decisions/{dr_id}/execute", json={})
    assert r.status_code == 200
    ex = r.json()
    if ex["intervention_action"] is not None:
        assert ex["execution_status"] == "ACCEPTED"

        # 5) record an outcome
        r = client.post(f"/decisions/{dr_id}/outcome", json={
            "result": "NOT_RECOVERED", "recovery_amount": "0",
        })
        assert r.status_code == 200
        assert r.json()["outcome_result"] == "NOT_RECOVERED"

        # 6) re-evaluate the case -> a new decision cycle
        r = client.post(f"/cases/{case_id}/reevaluate", json={})
        assert r.status_code == 200
        assert r.json()["cycle_number"] == 2
        dr2_id = r.json()["decision_record_id"]
        assert dr2_id != dr_id

    # GET /cases/{id} returns the whole audit chain
    r = client.get(f"/cases/{case_id}")
    assert r.status_code == 200
    case_audit = r.json()
    assert case_audit["recovery_case_id"] == case_id
    assert len(case_audit["cycles"]) >= 1
    # previous_cycles is populated on cycle >= 2
    if len(case_audit["cycles"]) >= 2:
        assert len(case_audit["cycles"][1]["previous_cycles"]) == 1


def test_evaluate_unknown_payment_404(api_client):
    client, _ = api_client
    import uuid

    r = client.post(f"/payments/{uuid.uuid4()}/evaluate", json={})
    assert r.status_code == 404


def test_ineligible_amount_returns_422(api_client):
    client, db = api_client
    m, cust, _ = _merchant_customer_policy(db, "inelig")
    r = client.post("/payments", json={
        "merchant_id": str(m.id), "customer_id": cust,
        "amount": "3.00", "currency": "INR", "payment_method": "CARD",
        "failure_category": "TEMPORARY", "failure_code": "SIM_GATEWAY_TIMEOUT",
    })
    payment_id = r.json()["id"]
    r = client.post(f"/payments/{payment_id}/evaluate", json={})
    assert r.status_code == 422
    assert "eligib" in r.json()["detail"].lower()


def test_predictions_come_from_the_real_trained_model(api_client):
    """Phase 8 audit: the live /evaluate path must be backed by the actual
    trained T-learner (per-action logistic regression on real
    TrainingExample data), not a naive/heuristic stand-in — and must
    produce a genuinely varying, non-degenerate probability surface
    across the three candidate actions."""
    client, db = api_client
    m, cust, _pol = _merchant_customer_policy(db, "mlcheck")

    r = client.post("/payments", json={
        "merchant_id": str(m.id), "customer_id": cust,
        "amount": "1800.00", "currency": "INR", "payment_method": "CARD",
        "failure_category": "TEMPORARY", "failure_code": "SIM_GATEWAY_TIMEOUT",
    })
    payment_id = r.json()["id"]
    r = client.post(f"/payments/{payment_id}/evaluate", json={})
    assert r.status_code == 200, r.text
    audit = r.json()["audit"]

    mv = audit["model_version"]
    assert mv is not None
    # the approved Phase 4 selection (docs: "The T-learner was selected"),
    # not a placeholder/naive-prior model
    assert mv["algorithm"] == "logistic_regression_per_action"
    assert mv["model_name"] == "recovery-t-learner-logreg"
    assert mv["status"] == "PROMOTED"

    probs = {a["action"]: a["recovery_probability"] for a in audit["actions_considered"]}
    assert set(probs) == {"RETRY", "MESSAGE", "NO_ACTION"}
    for p in probs.values():
        assert 0.0 <= p <= 1.0
    # a real per-action model disagrees across heads; a constant/naive
    # stand-in would not
    assert len({round(p, 6) for p in probs.values()}) > 1

    # Prediction rows persisted for this decision reference this exact
    # ModelVersion — not just the API response shape
    from backend.models.decision import DecisionRecord, Prediction

    dr = db.query(DecisionRecord).filter_by(id=audit["decision_record_id"]).one()
    preds = db.query(Prediction).filter_by(decision_record_id=dr.id).all()
    assert len(preds) == 3
    assert all(str(p.model_version_id) == mv["id"] for p in preds)


def test_experiment_treatment_arm_uses_its_validated_model(api_client):
    """Phase 9: a case assigned to TREATMENT with an ``experimental_config_ref``
    must be evaluated with that VALIDATED ModelVersion instead of the
    production PROMOTED default — while a CONTROL/unassigned case keeps
    using the default. The experiment must never change EIRV, policy, or
    action-selection behavior, only which model supplies probabilities."""
    import uuid

    client, db = api_client
    from backend.repositories.core import PaymentRepository, RecoveryCaseRepository
    from backend.repositories.governance import ExperimentRepository

    m, cust, _pol = _merchant_customer_policy(db, "expt")

    # a second, VALIDATED-but-not-PROMOTED model — the experimental candidate
    tr = train_uplift_model(db, kind="t_learner", version="expt-v1", seed=99)
    ModelVersionRepository(db).transition_status(tr.model_version, "VALIDATED")
    db.commit()
    experimental_mv_id = tr.model_version.id

    # create the failed payment via the normal API path
    r = client.post("/payments", json={
        "merchant_id": str(m.id), "customer_id": cust,
        "amount": "1800.00", "currency": "INR", "payment_method": "CARD",
        "failure_category": "TEMPORARY", "failure_code": "SIM_GATEWAY_TIMEOUT",
    })
    payment_id = r.json()["id"]
    payment = PaymentRepository(db).get(uuid.UUID(payment_id))

    # pre-open the case and assign it to TREATMENT referencing the
    # experimental model — what a real experiment set-up does before the
    # case's first evaluation.
    case = RecoveryCaseRepository(db).open_case(payment=payment, amount_at_risk=payment.amount)
    exp = ExperimentRepository(db).create(name=f"phase9-treatment-{payment_id[:8]}")
    ExperimentRepository(db).assign(
        experiment_id=exp.id, recovery_case_id=case.id, arm="TREATMENT",
        experimental_config_ref=experimental_mv_id,
    )
    db.commit()

    r = client.post(f"/payments/{payment_id}/evaluate", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recovery_case_id"] == str(case.id)
    assert body["audit"]["model_version"]["id"] == str(experimental_mv_id)
    assert body["audit"]["model_version"]["status"] == "VALIDATED"

    # a CONTROL / unassigned case still uses the production default
    r2 = client.post("/payments", json={
        "merchant_id": str(m.id), "customer_id": cust,
        "amount": "1800.00", "currency": "INR", "payment_method": "CARD",
        "failure_category": "TEMPORARY", "failure_code": "SIM_GATEWAY_TIMEOUT",
    })
    payment_id_2 = r2.json()["id"]
    r2 = client.post(f"/payments/{payment_id_2}/evaluate", json={})
    assert r2.status_code == 200, r2.text
    assert r2.json()["audit"]["model_version"]["id"] != str(experimental_mv_id)
    assert r2.json()["audit"]["model_version"]["status"] == "PROMOTED"

    # re-evaluating the TREATMENT case on a second cycle keeps the SAME
    # experimental model — the assignment inherits across cycles unchanged
    from backend.services import recovery_flow as flow

    if body["audit"]["intervention_action"] is not None:
        flow.execute_decision(db, decision_record_id=uuid.UUID(body["decision_record_id"]))
        flow.record_outcome(
            db, decision_record_id=uuid.UUID(body["decision_record_id"]),
            result="NOT_RECOVERED",
        )
        db.commit()
        r3 = client.post(f"/cases/{case.id}/reevaluate", json={})
        assert r3.status_code == 200, r3.text
        assert r3.json()["audit"]["model_version"]["id"] == str(experimental_mv_id)


def test_no_promoted_model_returns_409(tmp_path):
    """A DB with no PROMOTED ModelVersion -> evaluate returns 409."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk(c, _):  # noqa: ANN001
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    m, cust, _ = _merchant_customer_policy(db, "nomodel")
    from backend.services import recovery_flow as flow

    pay = flow.ingest_failed_payment(
        db, merchant_id=m.id, customer_id=cust, amount=Decimal("1500"),
        currency="INR", payment_method="CARD",
        failure_category="TEMPORARY", failure_code="SIM_GATEWAY_TIMEOUT",
    )
    db.commit()

    def _override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        r = client.post(f"/payments/{pay.id}/evaluate", json={})
        assert r.status_code == 409
        assert "promote" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        db.close()
        eng.dispose()
