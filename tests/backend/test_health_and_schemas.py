"""Backend startup, /health, and ORM->schema mapping."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.models import enums
from backend.repositories import (
    CustomerRepository,
    DecisionCycleRepository,
    MerchantRepository,
    ModelVersionRepository,
    PaymentRepository,
    RecoveryCaseRepository,
)
from backend.schemas import DecisionRecordRead, MerchantRead, PaymentEventRead


def test_health_endpoint_ok():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "db" in body  # reported non-fatally


def test_merchant_read_schema_maps_from_orm(db):
    m = MerchantRepository(db).create(name="ACME")
    dto = MerchantRead.model_validate(m)
    assert dto.display_id == m.display_id and dto.id == m.id


def test_decision_record_read_schema_nested(db):
    m = MerchantRepository(db).create(name="ACME")
    CustomerRepository(db).create(customer_id="C-1", merchant_id=m.id)
    p = PaymentRepository(db).create(
        merchant_id=m.id, customer_id="C-1", amount=Decimal("500"),
        currency="INR", status=enums.PaymentStatus.FAILED.value,
    )
    case = RecoveryCaseRepository(db).open_case(payment=p, amount_at_risk=p.amount)
    mv = ModelVersionRepository(db).create(
        model_role="r", model_name="s", version="v1",
        status=enums.ModelVersionStatus.VALIDATED.value,
    )
    dc = DecisionCycleRepository(db)
    dr = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
    for a in ("RETRY", "MESSAGE", "NO_ACTION"):
        dc.add_prediction(
            decision_record=dr, action=a, recovery_probability=Decimal("0.4"),
            model_version_id=mv.id, feature_snapshot={"a": a},
        )
    dc.finalize(decision_record=dr, recommended_action="RETRY", final_action="RETRY")
    db.refresh(dr)
    dto = DecisionRecordRead.model_validate(dr)
    assert dto.recommended_action == "RETRY" and dto.final_action == "RETRY"
    assert len(dto.predictions) == 3
    assert all(pr.model_version_id == mv.id for pr in dto.predictions)
    # DecisionRecordRead has no model_version field of its own
    assert "model_version_id" not in DecisionRecordRead.model_fields
